(function () {
  const LINK_TEXT = "was ist meine aufgabe?";
  const SIDEBAR_ENDPOINT = "/challenge/api/task-sidebar";

  let isRequestInFlight = false;

  const matchLinkByLabel = (element) =>
    element?.textContent?.trim().toLowerCase() === LINK_TEXT;

  const openSidebar = async () => {
    if (isRequestInFlight) return;
    isRequestInFlight = true;
    try {
      const response = await fetch(SIDEBAR_ENDPOINT, {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
        },
        credentials: "include",
      });
      if (!response.ok) {
        throw new Error(`Sidebar request failed (${response.status})`);
      }
    } catch (error) {
      console.error("Failed to open Chainlit sidebar:", error);
    } finally {
      isRequestInFlight = false;
    }
  };

  const attachHandler = (link) => {
    if (!link || link.dataset.taskSidebarBound === "true") return;
    link.dataset.taskSidebarBound = "true";
    link.addEventListener("click", (event) => {
      event.preventDefault();
      openSidebar();
    });
  };

  const scanAndBind = () => {
    document
      .querySelectorAll("a")
      .forEach((anchor) => matchLinkByLabel(anchor) && attachHandler(anchor));
  };

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", scanAndBind, { once: true });
  } else {
    scanAndBind();
  }

  const observer = new MutationObserver(scanAndBind);
  observer.observe(document.documentElement, {
    childList: true,
    subtree: true,
  });
})();
