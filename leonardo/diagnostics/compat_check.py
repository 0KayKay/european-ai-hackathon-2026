"""Check a runtime against a model on a real GPU, without loading weights.

Ported from Junghwa Lee's GLM-5.3-Flash preflight (Jules0315/Guardrails-vllm, candidates/glm53/.../preflight.py)
and generalised to any profile: CUDA works, vLLM knows the model's architecture, and for sparse-MLA models
(GLM) which attention backend vLLM selects, the Triton sparse-MLA kernel against a PyTorch reference, and the
TileLang mHC kernel against its PyTorch reference. Runs inside the runtime's container; reads only the
checkpoint's config.json (COMPAT_CONFIG). Prints one JSON line per check and a final report.
"""
import importlib
import importlib.metadata
import inspect
import json
import math
import os
import socket
import time
import traceback

import torch
import vllm
from vllm.config import VllmConfig, set_current_vllm_config
from vllm.model_executor.models import ModelRegistry
from vllm.platforms import current_platform


def run_check(report, name, function):
    try:
        report[name] = {"passed": True, "result": function()}
    except Exception as error:
        report[name] = {"passed": False, "error_type": type(error).__name__, "error": str(error),
                        "traceback": traceback.format_exc()}
    print(json.dumps({name: report[name]}), flush=True)


def main():
    with open(os.environ["COMPAT_CONFIG"]) as handle:
        cfg = json.load(handle)
    text_cfg = cfg.get("text_config", cfg)
    architecture = (cfg.get("architectures") or ["?"])[0]
    sparse_mla = "index_topk" in text_cfg  # GLM's sparse MLA: an index selects the top-k keys
    report = {"test": "runtime/model compatibility; no weights loaded", "profile": os.environ.get("COMPAT_PROFILE"),
              "job_id": os.environ.get("SLURM_JOB_ID"), "node": socket.gethostname(),
              "started_at_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
              "architecture": architecture, "sparse_mla": sparse_mla, "versions": {}}
    for name in ("vllm", "torch", "transformers", "flashinfer-python", "tilelang"):
        try:
            report["versions"][name] = importlib.metadata.version(name)
        except importlib.metadata.PackageNotFoundError:
            report["versions"][name] = None
    required = ["gpu", "model_import"]

    def gpu_check():
        torch.cuda.init()
        x = torch.ones((32, 32), device="cuda", dtype=torch.bfloat16)
        y = x @ x
        torch.cuda.synchronize()
        assert y[0, 0].item() == 32
        props = torch.cuda.get_device_properties(0)
        return {"name": props.name, "compute_capability": list(torch.cuda.get_device_capability(0)),
                "total_memory_bytes": props.total_memory, "bf16_matmul": "passed"}

    run_check(report, "gpu", gpu_check)

    def model_import():
        assert architecture in ModelRegistry.get_supported_archs(), architecture + " is not in this vLLM's model registry"
        from vllm.model_executor.models.interfaces import supports_pp
        entry = ModelRegistry.models[architecture]
        cls = getattr(importlib.import_module(entry.module_name), entry.class_name)
        return {"module": entry.module_name, "class": cls.__name__, "supports_pipeline_parallel": supports_pp(cls)}

    run_check(report, "model_import", model_import)

    if report["gpu"]["passed"] and sparse_mla:
        capability = current_platform.get_device_capability()
        required.append("sparse_backend_selection")

        def choose_sparse_backend():
            from vllm.v1.attention.selector import get_attn_backend
            signature = inspect.signature(get_attn_backend)
            kwargs = {"head_size": int(text_cfg.get("kv_lora_rank", 512))
                      + (0 if text_cfg.get("mla_nope") else int(text_cfg.get("qk_rope_head_dim", 0))),
                      "dtype": torch.bfloat16, "kv_cache_dtype": "auto", "use_mla": True, "use_sparse": True}
            if "num_heads" in signature.parameters:
                kwargs["num_heads"] = int(text_cfg.get("num_attention_heads", 64))
            with set_current_vllm_config(VllmConfig()):
                backend = get_attn_backend(**kwargs)
            return {"backend": backend.__name__, "head_size": kwargs["head_size"], "selector": str(signature)}

        run_check(report, "sparse_backend_selection", choose_sparse_backend)
        selected = report["sparse_backend_selection"]
        if selected["passed"] and "Triton" in selected["result"]["backend"]:
            required.append("triton_sparse_kernel")

            def sparse_kernel_check():
                from vllm.v1.attention.ops.triton_mla_sparse_kernel import triton_mla_sparse_attention
                torch.manual_seed(42)
                q = torch.randn((2, 4, 512), device="cuda", dtype=torch.bfloat16)
                kv = torch.randn((64, 1, 512), device="cuda", dtype=torch.bfloat16)
                indices = torch.tensor([list(range(16)), list(range(16, 32))], device="cuda", dtype=torch.int32).unsqueeze(1)
                scale = 1 / math.sqrt(512)
                result = triton_mla_sparse_attention(q, kv, indices, sm_scale=scale, num_kv_splits=1)
                chosen = kv[indices[:, 0].long(), 0].float()
                scores = torch.einsum("thd,tkd->thk", q.float(), chosen) * scale
                expected = torch.einsum("thk,tkd->thd", torch.softmax(scores, dim=-1), chosen)
                torch.cuda.synchronize()
                torch.testing.assert_close(result.float(), expected, rtol=0.02, atol=0.02)
                return {"shape": list(result.shape), "reference": "PyTorch FP32 sparse attention",
                        "max_absolute_error": (result.float() - expected).abs().max().item()}

            run_check(report, "triton_sparse_kernel", sparse_kernel_check)
        for module_name, class_name in (("flashattn_mla_sparse", "FlashAttnMLASparseBackend"),
                                        ("flashinfer_mla_sparse", "FlashInferMLASparseTRTLLMBackend"),
                                        ("flashinfer_mla_sparse_sm90", "FlashInferMLASparseSM90Backend"),
                                        ("flashmla_sparse", "FlashMLASparseBackend")):
            def backend_support(module_name=module_name, class_name=class_name):
                cls = getattr(importlib.import_module("vllm.v1.attention.backends.mla." + module_name), class_name)
                return {"supports_this_gpu": cls.supports_compute_capability(capability),
                        "head_sizes": cls.get_supported_head_sizes()}
            run_check(report, "backend " + class_name, backend_support)  # informational

    if report["gpu"]["passed"] and report["versions"]["tilelang"]:
        try:
            from vllm.model_executor.kernels.mhc import mhc_pre_tilelang, mhc_pre_torch
        except ImportError:
            mhc_pre_tilelang = None
        if mhc_pre_tilelang is not None:
            required.append("mhc_pre_kernel")

            def mhc_check():
                import tilelang
                import tilelang.webgpu  # noqa: F401  (missing in the backport image's own TileLang)
                torch.manual_seed(123)
                residual = torch.randn((8, 4, 4096), device="cuda", dtype=torch.bfloat16)
                fn = torch.randn((24, 4 * 4096), device="cuda", dtype=torch.float32) * 0.01
                scale = torch.ones((3,), device="cuda", dtype=torch.float32)
                base = torch.randn((24,), device="cuda", dtype=torch.float32) * 0.01
                arguments = (residual, fn, scale, base, 1e-6, 1e-6, 1e-6, 2.0, 20)
                with set_current_vllm_config(VllmConfig()):
                    actual = mhc_pre_tilelang(*arguments)
                    expected = mhc_pre_torch(*arguments)
                torch.cuda.synchronize()
                assert len(actual) == len(expected)
                errors = []
                for a, b in zip(actual, expected):
                    torch.testing.assert_close(a.float(), b.float(), rtol=0.02, atol=0.02)
                    errors.append((a.float() - b.float()).abs().max().item())
                return {"tilelang_path": tilelang.__file__, "reference": "vLLM PyTorch mHC implementation",
                        "max_absolute_errors": errors}

            run_check(report, "mhc_pre_kernel", mhc_check)

    report["weights_loaded"] = False
    report["required_checks"] = required
    report["compatible"] = all(report.get(name, {}).get("passed", False) for name in required)
    print("COMPAT_REPORT " + json.dumps(report), flush=True)
    print("COMPAT_" + ("OK" if report["compatible"] else "FAILED"), flush=True)
    raise SystemExit(0 if report["compatible"] else 2)


if __name__ == "__main__":
    main()
