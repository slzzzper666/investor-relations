"""階段五(2)：推播到 Telegram 與 Discord。"""
import html
import time

import config
from ir.logger import get_logger
from ir.mops import Conference
from ir.net import get_session

log = get_logger("ir.notify")

_TG_LIMIT = 4000        # Telegram 上限 4096，留安全邊際
_DC_DESC_LIMIT = 3800   # Discord embed description 上限 4096
_TG_HARD = 4096
_DC_HARD = 2000         # Discord content（非 embed）上限


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


# ── 純文字推播（財報雷達子系統用：行事曆、財報/總經結果）─────


def _split(text: str, limit: int) -> list[str]:
    """依行切割訊息，避免超過平台長度上限。"""
    if len(text) <= limit:
        return [text]
    parts, buf = [], ""
    for line in text.split("\n"):
        if len(buf) + len(line) + 1 > limit:
            parts.append(buf)
            buf = line
        else:
            buf = f"{buf}\n{line}" if buf else line
    if buf:
        parts.append(buf)
    return parts


def push_text_telegram(text: str) -> bool:
    if not config.TELEGRAM_BOT_TOKEN or not config.TELEGRAM_CHAT_ID:
        log.info("【TG 未設定，console 預覽】\n%s", text)
        return False
    url = f"https://api.telegram.org/bot{config.TELEGRAM_BOT_TOKEN}/sendMessage"
    ok = True
    for part in _split(text, _TG_HARD):
        for attempt in range(3):
            try:
                r = get_session().post(
                    url,
                    json={"chat_id": config.TELEGRAM_CHAT_ID, "text": part,
                          "disable_web_page_preview": True},
                    timeout=30,
                )
                if r.status_code == 429:
                    wait = r.json().get("parameters", {}).get("retry_after", 5)
                    time.sleep(wait + 1)
                    continue
                r.raise_for_status()
                break
            except Exception as e:
                log.warning("TG 純文字推播失敗（第 %d 次）：%s", attempt + 1, e)
                time.sleep(3)
        else:
            ok = False
    if ok:
        log.info("TG 純文字推播成功（%d 字）", len(text))
    return ok


def push_text_discord(text: str) -> bool:
    if not config.DISCORD_WEBHOOK_URL:
        log.info("【DC 未設定，console 預覽略過】")
        return False
    ok = True
    for part in _split(text, _DC_HARD):
        for attempt in range(3):
            try:
                r = get_session().post(
                    config.DISCORD_WEBHOOK_URL, json={"content": part}, timeout=30
                )
                if r.status_code == 429:
                    wait = r.json().get("retry_after", 5)
                    time.sleep(float(wait) + 1)
                    continue
                r.raise_for_status()
                break
            except Exception as e:
                log.warning("DC 純文字推播失敗（第 %d 次）：%s", attempt + 1, e)
                time.sleep(3)
        else:
            ok = False
        time.sleep(0.5)
    if ok:
        log.info("DC 純文字推播成功（%d 字）", len(text))
    return ok


def push_text(text: str) -> None:
    """同時推播純文字到 TG 與 DC（財報雷達行事曆/結果用）。"""
    push_text_telegram(text)
    push_text_discord(text)
