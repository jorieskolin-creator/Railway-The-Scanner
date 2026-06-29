import { execFileSync } from "node:child_process";

const EXPECTED_REPO = "jorieskolin-creator/Railway-The-Scanner";
const EXPECTED_BRANCH = "main";

function runGit(args) {
  return execFileSync("git", args, { encoding: "utf8" }).trim();
}

function normalizeRemote(url) {
  return url
    .replace(/^git@github\.com:/, "https://github.com/")
    .replace(/\.git$/, "")
    .replace(/\/$/, "");
}

const originUrl = normalizeRemote(runGit(["remote", "get-url", "origin"]));
const branchName = runGit(["branch", "--show-current"]);
const expectedUrl = `https://github.com/${EXPECTED_REPO}`;

if (originUrl !== expectedUrl) {
  console.error(
    [
      "Production remote check failed.",
      `Expected origin: ${expectedUrl}.git`,
      `Actual origin:   ${originUrl}.git`,
      "",
      "Fix with:",
      `git remote set-url origin ${expectedUrl}.git`,
    ].join("\n"),
  );
  process.exit(1);
}

if (branchName !== EXPECTED_BRANCH) {
  console.error(
    [
      "Production branch check failed.",
      `Expected branch: ${EXPECTED_BRANCH}`,
      `Actual branch:   ${branchName}`,
      "",
      `Switch with: git checkout ${EXPECTED_BRANCH}`,
    ].join("\n"),
  );
  process.exit(1);
}

console.log(`Production remote verified: ${EXPECTED_REPO} (${EXPECTED_BRANCH})`);
