# config/

**Owner: both laptops. Change only by agreement.**

- `settings.example.json` is committed and contains no secrets.
- `settings.json` is your real copy. It's **gitignored** and holds the passphrase and your serial port.

```
cp config/settings.example.json config/settings.json
```

Keys named `mac` / `windows` are picked by OS (`sys.platform` `darwin` → mac, `win32` → windows). `llm.profile` picks the Bonsai server: `mac` (Mac mini, 9B) or `nv_backup` (NV laptop, 4B q2_0).
