"""Styling and small HTML components shared by the Streamlit pages (tokens from the design canvas)."""

from __future__ import annotations

import html

import streamlit as st

TOKENS = {
    "accent": "#0B6176", "tint": "#E5F1F4", "bg": "#F4F6F7", "ink": "#14222B", "muted": "#56656F", "line": "#D3DCE1",
    "licensed": "#4450AE", "licensed_bg": "#ECEEFA", "upload": "#9C5D0C", "upload_bg": "#F8F0E4",
    "public": "#2A7449", "public_bg": "#E8F3EC", "high": "#B3321E", "high_bg": "#FBEAE6", "med": "#845E00", "med_bg": "#F7F0DC",
}

CSS = """
<link rel="stylesheet" href="https://fonts.googleapis.com/css2?family=IBM+Plex+Mono:wght@400;500&family=IBM+Plex+Sans+Condensed:wght@600&family=IBM+Plex+Sans:wght@400;500;600&display=swap">
<style>
html, body, [class*="css"], .stApp, .stMarkdown, button, input, textarea, select { font-family: 'IBM Plex Sans', system-ui, sans-serif !important; }
.stApp { background: #F4F6F7; color: #14222B; }
h1, h2, h3 { font-family: 'IBM Plex Sans Condensed', 'IBM Plex Sans', sans-serif !important; font-weight: 600 !important; color: #14222B; }
.block-container { padding-top: 3.2rem; max-width: 1280px; }
h1 { font-size: 30px !important; line-height: 1.2 !important; padding: 4px 0 6px 0 !important; }
a { color: #0B6176; }
.mr-eyebrow { font-family: 'IBM Plex Mono', monospace; font-size: 12px; letter-spacing: .03em; color: #56656F; }
.mr-sub { color: #56656F; font-size: 14px; margin-top: -6px; }
.mr-card { background: #FFFFFF; border: 1px solid #D3DCE1; border-radius: 12px; padding: 16px 18px; margin-bottom: 14px; }
.mr-card h4 { margin: 0 0 10px 0; font-size: 15px; font-weight: 600; font-family: 'IBM Plex Sans', sans-serif !important; }
.mr-kpi { background: #FFFFFF; border: 1px solid #D3DCE1; border-radius: 12px; padding: 14px 16px; height: 100%; }
.mr-kpi .l { font-size: 12.5px; color: #56656F; display: block; }
.mr-kpi .v { font-family: 'IBM Plex Sans Condensed', sans-serif; font-weight: 600; font-size: 30px; line-height: 1.25; display: block; }
.mr-kpi .n { font-size: 12.5px; color: #56656F; display: block; }
.pill { font-family: 'IBM Plex Mono', monospace; font-size: 11px; border-radius: 999px; padding: 2px 9px; white-space: nowrap; display: inline-block; }
.mr-feed { display: grid; grid-template-columns: 84px minmax(0,1fr); gap: 10px 12px; align-items: start; padding: 10px 0; border-bottom: 1px solid #D3DCE1; }
.mr-feed:last-child { border-bottom: 0; }
.mr-feed .t { font-size: 14px; }
.mr-feed .m { font-size: 12.5px; color: #56656F; display: block; margin-top: 2px; }
.mr-table { width: 100%; border-collapse: collapse; font-size: 13.5px; }
.mr-table th { text-align: left; font-size: 12px; color: #56656F; font-weight: 600; padding: 8px 10px; border-bottom: 1px solid #D3DCE1; }
.mr-table td { padding: 10px; border-bottom: 1px solid #E6ECEF; vertical-align: top; }
.mr-table tr.you td { background: #E5F1F4; }
.mr-report { background: #FFFFFF; border: 1px solid #D3DCE1; border-radius: 12px; padding: 22px 26px; line-height: 1.65; font-size: 15px; }
.mr-report h2 { font-size: 26px !important; padding: 0 0 8px 0 !important; }
.mr-report h3 { font-size: 17px !important; margin: 14px 0 4px 0; padding: 0 !important; }
.mr-report p { margin: 0 0 12px 0; }
del { background: #FBEAE6; color: #8E2716; text-decoration: line-through; text-decoration-color: #B3321E; }
ins { background: #E3F1E8; color: #14222B; text-decoration: none; border-bottom: 2px solid #2A7449; }
sup { font-family: 'IBM Plex Mono', monospace; font-size: 10px; color: #0B6176; }
sup a { text-decoration: none; }
span.judgement { background: #F7F0DC; border-radius: 3px; }
.mr-flag { background: #F7F0DC; border-radius: 10px; padding: 12px 14px; font-size: 13.5px; line-height: 1.5; margin: 6px 0 10px 0; }
.mr-check { display: flex; gap: 10px; font-size: 13.5px; padding: 3px 0; }
.mr-tl { display: grid; grid-template-columns: 96px 14px minmax(0,1fr); gap: 4px 10px; }
.mr-tl .d { font-family: 'IBM Plex Mono', monospace; font-size: 12px; color: #56656F; padding-top: 2px; }
.mr-tl .dot { width: 10px; height: 10px; border-radius: 999px; background: #0B6176; margin-top: 6px; }
.mr-tl .b { padding-bottom: 14px; font-size: 14px; }
.mr-tl .b small { display: block; color: #56656F; font-size: 12.5px; margin-top: 2px; }
.mr-chip { font-family: 'IBM Plex Mono', monospace; font-size: 11.5px; border-radius: 999px; padding: 3px 10px; background: #EDF1F3; display: inline-block; margin: 0 6px 6px 0; }
.mr-foot, p.mr-foot { font-size: 12.5px !important; color: #56656F; line-height: 1.5; }
.mr-sub { font-size: 14px !important; }
div[data-testid="stMetric"] { background: #FFFFFF; border: 1px solid #D3DCE1; border-radius: 12px; padding: 12px 14px; }
.stButton > button[kind="primary"], .stDownloadButton > button[kind="primary"], .stFormSubmitButton > button[kind="primary"] { background: #0B6176; border-color: #0B6176; }
</style>
"""

LEVEL_STYLE = {"high": ("#FBEAE6", "#B3321E", "High"), "medium": ("#F7F0DC", "#845E00", "Medium"),
               "low": ("#E5F1F4", "#0B6176", "Low"), "noise": ("#EDF1F3", "#56656F", "Noise"),
               "conflict": ("#ECEEFA", "#4450AE", "Conflict")}
KIND_STYLE = {"public": ("#E8F3EC", "#2A7449"), "licensed": ("#ECEEFA", "#4450AE"), "upload": ("#F8F0E4", "#9C5D0C"),
              "brief": ("#E5F1F4", "#0B6176")}
THREAT_STYLE = {"High": ("#FBEAE6", "#B3321E"), "Medium": ("#F7F0DC", "#845E00"), "Low": ("#E5F1F4", "#0B6176"),
                "Watch": ("#EDF1F3", "#56656F")}


def inject() -> None:
    st.markdown(CSS, unsafe_allow_html=True)


def esc(s) -> str:
    return html.escape(str(s if s is not None else ""))


def pill(text: str, bg: str, fg: str, border: bool = False) -> str:
    b = f"border:1px solid {fg};" if border else ""
    return f'<span class="pill" style="background:{bg};color:{fg};{b}">{esc(text)}</span>'


def level_pill(level: str) -> str:
    bg, fg, label = LEVEL_STYLE.get(level, LEVEL_STYLE["noise"])
    return pill(label, bg, fg)


def kind_pill(kind: str, label: str | None = None) -> str:
    bg, fg = KIND_STYLE.get(kind, KIND_STYLE["public"])
    return pill(label or kind, bg, fg)


def threat_pill(t: str) -> str:
    bg, fg = THREAT_STYLE.get(t, THREAT_STYLE["Watch"])
    return pill(t, bg, fg)


def kpi(label: str, value: str, note: str) -> str:
    return f'<div class="mr-kpi"><span class="l">{esc(label)}</span><span class="v">{esc(value)}</span><span class="n">{esc(note)}</span></div>'


def card(title: str, body_html: str, accent: str | None = None) -> str:
    top = f"border-top:3px solid {accent};" if accent else ""
    head = f"<h4>{esc(title)}</h4>" if title else ""
    return f'<div class="mr-card" style="{top}">{head}{body_html}</div>'


def md(html_str: str) -> None:
    st.markdown(html_str, unsafe_allow_html=True)
