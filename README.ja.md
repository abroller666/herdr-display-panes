# herdr-display-panes

[English](README.md)

[Herdr](https://herdr.dev) で tmux の `display-panes` を使えるようにするプラグインです。

![herdr-display-panes のデモ](assets/demo.gif)

キーを押すと、現在のタブのペイン配置図がポップアップで表示されます。
各ペインには左上から順に1文字のキーが割り当てられ、そのキーを押すとペインに移動します。
ホームポジションの段 (`a` `s` `d` `f` `g` `h` `j` `k` `l`)、上の段 (`w` … `p`)、
下の段 (`z` … `m`) の順で、最大 25 ペインまで対応します。

```
┌──────────────────────────────┐
│              a               │
│            claude            │
│      fix-login-redirect      │
└──────────────────────────────┘
┌──────────────┐┌──────────────┐
│      s       ││      d       │
│    codex     ││  ~/project   │
└──────────────┘└──────────────┘
Press a key to jump / q or Esc to close
```

- フォーカス中のペインは強調表示されます。
- エージェントのペインにはエージェント名とセッション名、シェルのペインには作業ディレクトリが表示されます。
- 画像プロトコルは使わずテキストで描画するので、どのターミナルでも動きます。
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
  "timeout": 10
}
```

- `labels`: ペインに割り当てるキー (左上から右下の順)。重複と `q` は無視されます。
  ラベルが付くのは最大 `labels` の文字数分のペインです。
- `timeout`: ポップアップが自動で閉じるまでの秒数。

## ライセンス

MIT
