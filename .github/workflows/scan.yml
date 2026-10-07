name: Upbit Spot Profit Tracking Bot

on:
  # 원하시는 실행 주기에 맞춰 cron을 설정하세요 (예: 매 15분마다 실행)
  schedule:
    - cron: '*/15 * * * *'
  workflow_dispatch: # 수동 실행 버튼 제공

jobs:
  scan:
    runs-on: ubuntu-latest
    permissions:
      contents: write # 💡 bot_state.json 자동 커밋을 위해 반드시 필요합니다.

    steps:
      - name: Checkout repository
        uses: actions/checkout@v4

      - name: Set up Python
        uses: actions/setup-python@v5
        with:
          python-version: '3.10'
          cache: 'pip'

      - name: Install dependencies
        run: |
          python -m pip install --upgrade pip
          if [ -f requirements.txt ]; then pip install -r requirements.txt; fi

      - name: Run Bot
        env:
          TELEGRAM_TOKEN: ${{ secrets.TELEGRAM_TOKEN }}
          TELEGRAM_CHAT_ID: ${{ secrets.TELEGRAM_CHAT_ID }}
        run: python bot.py  # 실행하시는 파이썬 파일명으로 맞춰주세요

      # 💡 봇 실행 후 변경된 포지션 상태(bot_state.json)를 리포지토리에 자동 커밋
      - name: Save State and Commit
        uses: stefanzweifel/git-auto-commit-action@v5
        with:
          commit_message: "Auto update bot_state.json [skip ci]"
          file_pattern: bot_state.json
