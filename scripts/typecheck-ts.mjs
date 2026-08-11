// `tsc` cannot run against an empty project list — it exits with TS18002
// rather than doing nothing — and there is no TypeScript in this repo until
// Phase 16 (docs/15 §3, T1.1 note A2).
//
// The lazy fix is a no-op script. The problem with a no-op is that it stays
// green after someone adds TypeScript, so the first components ship
// untypechecked and nobody learns that until much later.
//
// So this does not skip blindly: it skips only while there is genuinely no
// TypeScript, and fails the moment a .ts/.tsx file exists without a project
// reference to check it. The gate restores itself.

import { execFileSync } from 'node:child_process';
import { readdirSync, readFileSync } from 'node:fs';
import { join } from 'node:path';

const IGNORED = new Set(['node_modules', '.next', 'dist', '.venv', '.git', 'fixtures', 'docs']);

function findTypeScript(dir) {
  for (const entry of readdirSync(dir, { withFileTypes: true })) {
    if (entry.name.startsWith('.') || IGNORED.has(entry.name)) continue;
    const full = join(dir, entry.name);
    if (entry.isDirectory()) {
      const found = findTypeScript(full);
      if (found) return found;
    } else if (/\.(ts|tsx|mts|cts)$/.test(entry.name)) {
      return full;
    }
  }
  return null;
}

// Comment-stripping is crude but sufficient: this file only ever reads our own
// tsconfig.json, whose shape we control.
const tsconfig = JSON.parse(readFileSync('tsconfig.json', 'utf8').replace(/^\s*\/\/.*$/gm, ''));
const hasReferences = (tsconfig.references ?? []).length > 0;

if (hasReferences) {
  execFileSync('tsc', ['--build', 'tsconfig.json'], { stdio: 'inherit', shell: true });
  process.exit(0);
}

const stray = findTypeScript('.');
if (stray) {
  console.error(
    `typecheck: found TypeScript at ${stray} but tsconfig.json has no project references,\n` +
      'so nothing is being typechecked. Add the workspace to "references" in tsconfig.json\n' +
      'and give it a tsconfig of its own (docs/15 §3 — Phase 16 restores the TS half of\n' +
      '`make check`).',
  );
  process.exit(1);
}

console.log('typecheck: no TypeScript project yet — enabled by Phase 16 (docs/15 §3).');
