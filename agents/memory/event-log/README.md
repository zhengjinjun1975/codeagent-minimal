# event-log 原子

事件流原子：append-only 事件落盘 + 检查点 + 按事件回放。

## 定位

长任务（生成循环、训练链、采集）出事后，从"捞日志猜原因"改成"按事件重放"。对应 Harness Engineering 清单里的「事件流 + checkpoint 回放」。

## 能力

| 能力 | 入参 | 返回 data |
|---|---|---|
| `event.append` | session_key, kind, payload=None, outcome=None | seq |
| `event.checkpoint` | session_key, state | seq, state_digest |
| `event.timeline` | session_key, from_seq=0, to_seq=None | events, count |
| `event.replay` | session_key, to_seq=None | restored_state, up_to_seq |

## 落盘

`<store_dir>/<session_key>.jsonl`，一行一事件（UTF-8，append-only）。
`store_dir` 默认本目录下的 `_store/`。

## 依赖

无（depends_on 为空，纯标准库，数据不出厂）。

## 自测

```bash
python main.py --capability event.append --session_key demo --kind step --payload '{"a":1}'
python main.py --capability event.timeline --session_key demo
```
