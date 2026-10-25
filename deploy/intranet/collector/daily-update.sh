#!/usr/bin/env bash
# 蒐集主機每日排程:抓新聞與技術文獻 → 產生網站 → 打包內網版 → 推送到內網網頁主機。
# crontab 範例(每天 05:40):
#   40 5 * * * /opt/target-news/repo/deploy/intranet/collector/daily-update.sh >> /var/log/target-news.log 2>&1
set -euo pipefail

ENV_FILE="${ENV_FILE:-/etc/target-news/collector.env}"
set -a; source "$ENV_FILE"; set +a
cd "${REPO_DIR:?REPO_DIR not set}"
BUILD="${BUILD_DIR:-$REPO_DIR/build}"

echo "== $(date '+%F %T') start"
git pull --ff-only || echo "git pull skipped"

# 1) 新聞與技術文獻(寫入 reports/YYYY-MM-DD.json / .md)
python3 -m target_news_agent -v run --days 7

# 2) 財務儀表板(預設每週一更新財報;FIN_REFRESH=daily 則每天)
if [ "${FIN_REFRESH:-1}" = "daily" ] || [ "$(date +%u)" = "${FIN_REFRESH:-1}" ]; then
  python3 -m fin_analyzer -v fetch || echo "financial fetch failed; keeping the previous data"
  python3 -m fin_analyzer dashboard || echo "dashboard build failed; keeping the previous dashboard"
fi

# 3) 產生網站並打包成內網版(不連外部 CDN、無頁面加密,由 SSO 控管存取)
rm -rf "$BUILD/site"
python3 -m target_news_agent site -i reports -o "$BUILD/site/news" --root-redirect "$BUILD/site"
if [ -f reports/dashboard/index.html ]; then
  mkdir -p "$BUILD/site/financials"
  cp reports/dashboard/index.html reports/dashboard/dashboard.json reports/dashboard/comparison.csv fin_analyzer/assets/og.png "$BUILD/site/financials/"
fi
python3 -m target_news_agent intranet --src "$BUILD/site" --out "$BUILD/intranet"

# 4) 單向推送到網頁主機(只同步網站目錄)
rsync -a --delete "$BUILD/intranet/site/" "${WEB_HOST:?WEB_HOST not set}:${WEB_ROOT:-/srv/target-news/site}/"
echo "== $(date '+%F %T') done"
