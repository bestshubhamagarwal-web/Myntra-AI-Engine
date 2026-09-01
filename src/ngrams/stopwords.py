"""English + Hindi stopwords for n-gram jobs (EC-UI-07)."""

from __future__ import annotations

EN = frozenset(
    """
    a an the and or but if so as at by for from in into of on onto to with without
    is are was were be been being it this that these those i you we they he she
    my your our their me us them not no nor too very just than then also only
    can will would should could about over after before again more most some any
    all each few other such own same because until while during
    app store play google review reviews myntra
    """.split()
)

HI = frozenset(
    """
    hai hain ho hoga hogi tha thi the hun hoon kya kyun kaise
    ka ki ke ko se mein main me yeh ye woh wo aur ya nahi nahin
    ek do teen yehi wahi par pe pehle baad bhi to toh
    kuch sab apna apni uska uski iska iski
    """.split()
)

STOPWORDS = EN | HI
