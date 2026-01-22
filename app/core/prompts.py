NORMALIZE_DAILY_REPORT_PROMPT = """
以下は建設現場で録音された音声メモです。

東京都の公共工事で使用される施工日報向けに、
事実のみを簡潔かつ公的な文体で整形してください。

【ルール】
・口語表現を業務用日本語に変換
・推測、感情、主観は禁止
・事実が不明な項目は null
・簡潔
・JSONのみを出力（説明文は禁止）

【JSON形式】
{
  "start_time": "HH:MM または null",
  "end_time": "HH:MM または null",
  "work_performed": "作業内容（箇条書き）",
  "weather": "天候"
}
"""