# main.py

from fastapi import FastAPI, Request, HTTPException
from dotenv import load_dotenv
import os
from typing import List

from linebot import LineBotApi, WebhookParser
from linebot.models import MessageEvent, TextMessage, TextSendMessage
from linebot.exceptions import InvalidSignatureError

from openai import OpenAI

# ===== 質問リスト =====
QUESTIONS = [
    "希望エリアはどちらですか？（例：品川、新宿など）",
    "家賃の上限を教えてください。（例：10万円）",
    "間取りの希望を教えてください。（例：1LDK、2DKなど）",
    "駅から徒歩何分以内が良いですか？",
    "築年数の希望はありますか？（例：新築〜10年以内など）",
    "ペット可などの条件はありますか？",
    "職場（学校）までの通勤時間の希望はありますか？",
    "どんなライフスタイルですか？（静かに過ごしたい／駅近重視など）",
    "重視するポイントは？（家賃・広さ・場所・築年数など）",
    "入居希望時期はいつ頃ですか？"
]

# user_id -> {"index": 今何問目か, "answers": [(質問, 答え), ...]}
user_states: dict[str, dict] = {}

# ===== 初期化 =====
load_dotenv()
app = FastAPI()

LINE_CHANNEL_ACCESS_TOKEN = os.getenv("LINE_CHANNEL_ACCESS_TOKEN")
LINE_CHANNEL_SECRET = os.getenv("LINE_CHANNEL_SECRET")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")

if not LINE_CHANNEL_ACCESS_TOKEN or not LINE_CHANNEL_SECRET:
    raise RuntimeError("LINE のトークン/シークレットが設定されていません")

if not OPENAI_API_KEY:
    raise RuntimeError("OPENAI_API_KEY が設定されていません")

line_bot_api = LineBotApi(LINE_CHANNEL_ACCESS_TOKEN)
parser = WebhookParser(LINE_CHANNEL_SECRET)

openai_client = OpenAI(api_key=OPENAI_API_KEY)


# ===== 動作確認用エンドポイント =====
@app.get("/")
async def read_root():
    return {"message": "LINE Bot with GPT is running!"}


# ===== GPT に回答をまとめて送る関数 =====
async def summarize_with_gpt(answers: list[tuple[str, str]]) -> str:
    if not answers:
        return "まだ回答が入力されていないので、おすすめ物件を出せませんでした。もう一度「開始」と送ってやり直してください。"

    qa_text = ""
    for i, (q, a) in enumerate(answers, start=1):
        qa_text += f"Q{i}: {q}\nA{i}: {a}\n\n"

    prompt = f"""
以下は、賃貸物件を探しているユーザーの希望条件です。
この情報をもとに、日本の一般的な賃貸市場を想定して、
ユーザーに合いそうな「仮想の」おすすめ物件を3件提案してください。

それぞれについて、
- 物件名（仮でOK）
- 家賃の目安
- 間取り
- 最寄り駅と徒歩分数
- ユーザーの希望にマッチしている理由

を、箇条書きでわかりやすく日本語で説明してください。

ユーザーの回答は次のとおりです：

{qa_text}
"""

    res = openai_client.chat.completions.create(
        model="gpt-4o-mini",
        messages=[
            {"role": "system", "content": "あなたは親切で分かりやすく説明する不動産アドバイザーです。"},
            {"role": "user", "content": prompt},
        ],
    )

    return res.choices[0].message.content


# ===== GPTの結果を「複数バブル」に分割する関数 =====
def split_recommendations(text: str) -> List[TextSendMessage]:
    """
    GPT から返ってきたテキストを、
    ・1つ目: 冒頭の説明部分
    ・2つ目以降: 「###」で始まる物件ごとのブロック
    に分けて、複数の TextSendMessage にする。
    """
    blocks = [b.strip() for b in text.split("###") if b.strip()]
    messages: List[TextSendMessage] = []

    if not blocks:
        return [TextSendMessage(text="おすすめ物件をうまく生成できませんでした。もう一度お試しください。")]

    # 先頭ブロック（「以下は〜」などの説明）
    messages.append(TextSendMessage(text=blocks[0]))

    # 2個目以降は「###」を先頭に戻して物件カードっぽく
    for block in blocks[1:]:
        messages.append(TextSendMessage(text="### " + block))

    return messages


# ===== LINE Webhook エンドポイント =====
@app.post("/callback")
async def callback(request: Request):
    signature = request.headers.get("X-Line-Signature", "")
    body = (await request.body()).decode("utf-8")

    try:
        events = parser.parse(body, signature)
    except InvalidSignatureError:
        raise HTTPException(status_code=400, detail="Invalid signature")

    for event in events:
        if not isinstance(event, MessageEvent) or not isinstance(event.message, TextMessage):
            continue

        user_id = event.source.user_id
        text = event.message.text.strip()

        # 1. 「開始」で質問フロー開始
        if text == "開始":
            user_states[user_id] = {"index": 0, "answers": []}
            first_q = QUESTIONS[0]
            msg = (
                "不動産診断を開始します！\n\n"
                "これからいくつか質問をするので、順番に回答してください。\n"
                "途中でやめたいときは「終了」と送ってください。\n\n"
                f"Q1. {first_q}"
            )
            line_bot_api.reply_message(
                event.reply_token,
                TextSendMessage(text=msg),
            )
            continue

        # 2. まだ「開始」していない人
        if user_id not in user_states:
            line_bot_api.reply_message(
                event.reply_token,
                TextSendMessage(text="不動産診断を始めるには「開始」と送ってください。"),
            )
            continue

        state = user_states[user_id]
        idx = state["index"]

        # 3. 「終了」で途中までの回答を GPT に投げる
        if text == "終了":
            reply_text = await summarize_with_gpt(state["answers"])
            messages = split_recommendations(reply_text)
            line_bot_api.reply_message(
                event.reply_token,
                messages,
            )
            del user_states[user_id]
            continue

        # 4. 今の質問への回答を保存
        if idx < len(QUESTIONS):
            question = QUESTIONS[idx]
            state["answers"].append((question, text))
            state["index"] += 1
            idx = state["index"]

        # 5. まだ質問が残っている場合 → 次の質問を聞くだけ（GPT は呼ばない）
        if idx < len(QUESTIONS):
            next_q = QUESTIONS[idx]
            line_bot_api.reply_message(
                event.reply_token,
                TextSendMessage(text=f"Q{idx+1}. {next_q}"),
            )
            continue

        # 6. 全部回答し終わったので、ここで初めて GPT に1回だけ送る
        reply_text = await summarize_with_gpt(state["answers"])
        messages = split_recommendations(reply_text)
        line_bot_api.reply_message(
            event.reply_token,
            messages,
        )
        del user_states[user_id]

    return "OK"
