# rv-5 真实入口证据（final tree @ c6faff8，2026-09-11 10:32:53)

## 绿：iar agent list
```
codex (bin: codex, label: agent/codex)
  run: delivery=argv_tail, protocol=plain, writable
  deliberate: delivery=stdin, protocol=plain, read-only
  generate: delivery=argv_tail, protocol=plain, read-only
  repl: delivery=argv_tail, protocol=plain, writable
claude (bin: claude, label: agent/claude)
  run: delivery=argv_tail, protocol=claude-stream-json, writable
  deliberate: delivery=argv_tail, protocol=claude-stream-json, writable
  generate: delivery=argv_tail, protocol=plain, read-only
  repl: delivery=argv_tail, protocol=plain, writable
kimi (bin: kimi, label: agent/kimi)
  run: delivery=flag, protocol=plain, writable
  deliberate: delivery=stdin, protocol=plain, writable
  generate: delivery=flag, protocol=plain, read-only
  repl: delivery=flag, protocol=plain, writable
pi (bin: pi, label: agent/pi)
  run: delivery=stdin, protocol=pi-json-lines, writable
  deliberate: delivery=stdin, protocol=plain, read-only
  generate: delivery=stdin, protocol=plain, read-only
  repl: delivery=stdin, protocol=plain, writable
```

## 红：未注册 agent 名
```
exit=1
```
