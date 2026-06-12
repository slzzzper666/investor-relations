"""階段五(2)：推播到 Telegram 與 Discord。"""
import html

import config
from ir.logger import get_logger
from ir.mops import Conference
from ir.net import get_session

log = get_logger("ir.notify")

_TG_LIMIT = 4000        # Telegram 上限 4096，留安全邊際
_DC_DESC_LIMIT = 3800   # Discord embed description 上限 4096


def _links_line(conf: Conference, video_url: str, notion_url: str,
                fmt: str) -> str:
    items = []
    if conf.pdf_url:
        items.append(("📄 簡報 PDF", conf.pdf_url))
    if video_url:
        items.append(("🎥 影音", video_url))
    if notion_url:
        items.append(("📚 Notion 完整頁", notion_url))
    if fmt == "html":
        return "　".join(f'<a href="{u}">{t}</a>' for t, u in items)
    return "　".join(f"[{t}]({u})" for t, u in items)


def push_telegram(conf: Conference, analysis: dict, video_url: str,
                  notion_url: str) -> bool:
    market = "上市" if conf.market == "sii" else "上櫃"
    hl = "\n".join(f"• {html.escape(h)}" for h in analysis["highlights"])
    msg = (
        f"📊 <b>{html.escape(conf.company_name)}（{conf.stock_code}）法說會</b>"
        f"｜{market}｜{conf.date.isoformat()}\n\n"
        f"💡 {html.escape(analysis['one_liner'])}\n\n"
        f"{hl}\n\n"
    )
    if analysis.get("ai_view"):
        msg += f"🔭 <b>AI 觀點</b>\n{html.escape(analysis['ai_view'])}\n\n"
    msg += _links_line(conf, video_url, notion_url, "html")
    if len(msg) > _TG_LIMIT:
        msg = msg[:_TG_LIMIT] + "…"
    r = get_session().post(
        f"https://api.telegram.org/bot{config.TELEGRAM_BOT_TOKEN}/sendMessage",
        json={"chat_id": config.TELEGRAM_CHAT_ID, "text": msg,
              "parse_mode": "HTML", "disable_web_page_preview": True},
        timeout=30,
    )
    ok = r.status_code == 200 and r.json().get("ok")
    if not ok:
        log.warning("Telegram 推播失敗 %s：%s", conf.stock_code, r.text[:300])
    else:
        log.info("Telegram 已推播：%s %s", conf.stock_code, conf.company_name)
    return bool(ok)


def push_discord(conf: Conference, analysis: dict, video_url: str,
                 notion_url: str) -> bool:
    market = "上市" if conf.market == "sii" else "上櫃"
    hl = "\n".join(f"• {h}" for h in analysis["highlights"])
    desc = f"💡 {analysis['one_liner']}\n\n{hl}\n\n"
    if analysis.get("ai_view"):
        desc += f"🔭 **AI 觀點**\n{analysis['ai_view']}\n\n"
    desc += _links_line(conf, video_url, notion_url, "md")
    if len(desc) > _DC_DESC_LIMIT:
        desc = desc[:_DC_DESC_LIMIT] + "…"
    payload = {
        "embeds": [{
            "title": f"📊 {conf.company_name}（{conf.stock_code}）法說會"
                     f"｜{market}｜{conf.date.isoformat()}",
            "description": desc,
            "color": 0x2E6F40,
        }],
    }
    r = get_session().post(config.DISCORD_WEBHOOK_URL, json=payload, timeout=30)
    ok = r.status_code in (200, 204)
    if not ok:
        log.warning("Discord 推播失敗 %s：%s", conf.stock_code, r.text[:300])
    else:
        log.info("Discord 已推播：%s %s", conf.stock_code, conf.company_name)
    return ok
