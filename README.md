<div align="center">
<img width="1200" height="475" alt="GHBanner" src="https://github.com/user-attachments/assets/0aa67016-6eaf-458a-adb2-6e31a0763ed6" />
</div>

# Run and deploy your AI Studio app

This contains everything you need to run your app locally.

View your app in AI Studio: https://ai.studio/apps/drive/1_X7UCBtA1jBQYEAWOdkM8nQbYLF3ccCd

## Run Locally

**Prerequisites:**  Node.js


1. Install dependencies:
   `npm install`
2. Set `OPENAI_API_KEY` / `GPT_API_KEY` and `ANTHROPIC_API_KEY` in [.env.local](.env.local)
3. Run the app:
   `npm run dev`

## Production Remote Guard

Railway production deploys from `jorieskolin-creator/Railway-The-Scanner` on `main`.
Before pushing deployment changes, verify this checkout is pointed at the production repo:

```bash
npm run verify:remote
```

If the check fails, fix the remote with:

```bash
git remote set-url origin https://github.com/jorieskolin-creator/Railway-The-Scanner.git
```
