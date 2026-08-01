# Local `bugs/` screenshots (not in Git)

The `bugs/` directory holds local screenshots and repro media. It is listed in `.gitignore` so **nothing under `bugs/` is committed or pushed to GitHub**.

## How to attach files in Grok

The `@` picker hides gitignored paths by default. Prefix with `!` to include them:

```
@!bugs
@!bugs/MElon Basic Education5.png
```

Do **not** remove `bugs/` from `.gitignore` just to make `@bugs` work — use `@!bugs` instead.
