# herdr-display-panes

[English](README.md)

[Herdr](https://herdr.dev) で tmux の `display-panes` を使えるようにするプラグインです。

![herdr-display-panes のデモ](assets/demo.gif)

キーを押すと、現在のタブのペイン配置図がポップアップで表示されます。
各ペインには左上から順に1文字のキーが割り当てられ、そのキーを押すとペインに移動します。
ホームポジションの段 (`a` `s` `d` `f` `g` `h` `j` `k` `l`)、上の段 (`w` … `p`)、
下の段 (`z` … `m`) の順で、最大 25 ペインまで対応します。

```
┏━ ● current ━━━━━━━━━━━━━━━━━━┓┌──────────────────────────────┐
┃                              ┃│                              │
┃          ▐█  A  █▌           ┃│          ▐█  S  █▌           │
┃                              ┃│                              │
┃            claude            ┃│            codex             │
┃      fix-login-redirect      ┃│          review PR           │
┗━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━┛└──────────────────────────────┘
Press a key to jump / q or Esc to close
```

- **キーキャップ風のバッジ**: 各ペインのキーを色付きのバッジで表示します。
  エージェントのペインは色付き、シェルのペインはグレーです。小さいペインでは
  バッジも小さくなり、配置図全体で大きさがそろいます。
- **現在のペインが目立つ**: カーソルのあるペインは薄く色の付いた背景・太線の枠・
  `● current` の見出しで表示し、ほかのペインは暗く表示します。
- **エージェントの情報**: エージェントのペインにはエージェント名とセッション名、
  シェルのペインには作業ディレクトリを表示します。
- **配色は自分で決められる**: 全エージェント共通の色、またはエージェントごとの色を
  設定できます ([設定](#設定) を参照)。そのほかの色はターミナルの背景色・文字色に
  合わせるので、ライト/ダークどちらのテーマでも使えます。
- **テキストのみで描画**: 画像プロトコルを使わないので、どのターミナルでも動きます。
- `q`、`Esc`、または 10 秒経過でポップアップが閉じます。

## 必要なもの

- Herdr 0.9.2 以降
- macOS または Linux (動作確認は macOS)
- `python3` (3.8 以降、標準ライブラリのみ)

## インストール

```sh
herdr plugin install abroller666/herdr-display-panes
```

リリースを固定する場合は `--ref` を指定します:

```sh
herdr plugin install abroller666/herdr-display-panes --ref v0.1.0
```

`~/.config/herdr/config.toml` にキーバインドを追加します:

```toml
[[keys.command]]
key = "prefix+i"
type = "plugin_action"
command = "abroller666.display-panes.open"
description = "display panes"
```

`herdr server reload-config` で反映します。

## 設定

任意です。プラグインの設定ディレクトリ
(`herdr plugin config-dir abroller666.display-panes`) に `config.json` を置きます:

```json
{
  "labels": "asdfghjklwertyuiopzxcvbnm",
  "timeout": 10,
  "agent_color": "#9ece6a",
  "agent_colors": {
    "claude": "#d97757",
    "codex": "#7aa2f7"
  }
}
```

- `labels`: ペインに割り当てるキー (左上から右下の順)。重複と `q` は無視されます。
  ラベルが付くのは最大 `labels` の文字数分のペインです。
- `timeout`: ポップアップが自動で閉じるまでの秒数。
- `agent_color`: エージェントのペインの枠とバッジの色 (`#rrggbb` または `#rgb`)。
  省略時は緑です。
- `agent_colors`: エージェントごとの色 (エージェント名で指定、大文字小文字は区別しない)。
  書いていないエージェントは `agent_color` の色になります。

## ライセンス

MIT
