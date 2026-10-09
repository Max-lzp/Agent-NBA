# 2025-26 赛季名单

赛季名单只放这一个文件：`pipeline/vision/rosters/2025-26.json`。火箭和独行侠写在同一个文件里。同号的人靠球衣颜色先分队，再查这张表，所以不能拆成两个文件。

不要改 `pipeline/vision/roster.json`。那份按单场记出场名单，画面和事件清单还在用，留着和新名单对照。

```json
{
  "season": "2025-26",
  "source": "stats.nba.com commonteamroster + leaguedashplayerstats",
  "fetched_at": "2026-10-09",
  "teams": {
    "HOU": {
      "team_id": 1610612745,
      "name": "Houston Rockets",
      "players": [
        {
          "player_id": 1630000,
          "name": "Amen Thompson",
          "short_name": "Amen",
          "jersey": "1",
          "position": "G-F",
          "games_played": 0,
          "games_started": 0,
          "minutes_total": 0,
          "from": null,
          "to": null
        }
      ]
    },
    "DAL": {
      "team_id": 1610612742,
      "name": "Dallas Mavericks",
      "players": []
    }
  }
}
```

上面示例里的 `player_id`、`games_played`、`games_started`、`minutes_total` 是占位，提交时换成拉下来的真实数据。`team_id` 用上面这两个。

- `jersey` 一律是字符串。`"00"` 和 `"0"` 是两个号码，不要写成数字。
- `name` 写全名。`short_name` 沿用 `pipeline/vision/roster.json` 的写法，画面识别和事件清单用的是这个名字。已经出现过的人必须照旧：Amen、Durant、Smith、Eason、Sengun、Finney-Smith、Capela、Okogie、Sheppard、Tate、Holiday、Green、Crawford、Davison、Flagg、Christie、Gafford、Washington、Marshall、Powell、Martin、Cisse、Klay、Williams、Nembhard、Russell、Hardy、Davis。名单里还没有的人，按同样规则起短名。
- `minutes_total` 是赛季总上场分钟。每队 `players` 按它从多到少排，第一行是最常在场上的人。`games_played` 和 `games_started` 留着，不参与排序。
- `from` 和 `to` 是这个人用这个号码在这支队的起止日期，格式 `YYYY-MM-DD`。`to` 为 `null` 表示到拉取当天还在。赛季中被交易，或换了号码，就写两行，一行一段。
- 赛季内在火箭或独行侠登记过的人都收进来，包括双向合同和上场很少的人。分钟少的自然排在最后。
