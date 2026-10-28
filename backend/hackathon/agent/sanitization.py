import re
import unicodedata

from silverspeak import normalize_text
from emoji import demojize


def sanitize_text(text: str) -> str:
    text = unicodedata.normalize("NFC", text)
    text = normalize_text(text)
    text = demojize(text)
    text = re.sub("[\\u200B\\u200C\\u200D\\uFEFF]", "", text)
    text = re.sub(
        "\\b(?:[A-Za-z]\\s+)+[A-Za-z]\\b", lambda m: m.group(0).replace(" ", ""), text
    )
    text = re.sub("[\\U000E0000-\\U000E007F]", "", text)
    text = re.sub("[\\u202A-\\u202E\\u200E\\u200F\\u061C]", "", text)
    text = re.sub(
        "[\\u0300-\\u036F\\u1AB0-\\u1AFF\\u1DC0-\\u1DFF\\u20D0-\\u20FF\\uFE20-\\uFE2F]",
        "",
        text,
    )
    fullwidth_to_halfwidth = str.maketrans(
        "ＡＢＣＤＥＦＧＨＩＪＫＬＭＮＯＰＱＲＳＴＵＶＷＸＹＺａｂｃｄｅｆｇｈｉｊｋｌｍｎｏｐｑｒｓｔｕｖｗｘｙｚ０１２３４５６７８９",
        "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789",
    )
    text = text.translate(fullwidth_to_halfwidth)
    text = re.sub("[\\x00-\\x08\\x0B\\x0C\\x0E-\\x1F\\x7F-\\x9F]", "", text)
    text = re.sub("[ \\t]+", " ", text)
    text = re.sub("\\n{3,}", "\n\n", text)
    return text
