#!/usr/bin/env node
const { execSync } = require('child_process');
const fs = require('fs');
const path = require('path');

const pkgDir = __dirname;
const venvDir = path.join(pkgDir, '.venv');

console.log('🧠 Psyche post-install: Setting up local Python environment...');

// 1. Verify Python 3 is installed
let pythonCmd = 'python3';
try {
  execSync('python3 --version', { stdio: 'ignore' });
} catch (e) {
  try {
    execSync('python --version', { stdio: 'ignore' });
    pythonCmd = 'python';
  } catch (err) {
    console.error('❌ Error: Python 3 was not found on your system.');
    console.error('Psyche requires Python 3 to run its GraphRAG engine.');
    process.exit(1);
  }
}

// 2. Create local .venv virtualenv
if (!fs.existsSync(venvDir)) {
  console.log(`Creating virtual environment in: ${venvDir}`);
  try {
    execSync(`"${pythonCmd}" -m venv "${venvDir}"`, { stdio: 'inherit' });
  } catch (err) {
    console.error('❌ Failed to create Python virtual environment:', err.message);
    process.exit(1);
  }
}

// 3. Resolve pip path
const isWin = process.platform === 'win32';
const pipPath = isWin
  ? path.join(venvDir, 'Scripts', 'pip.exe')
  : path.join(venvDir, 'bin', 'pip');

// 4. Install dependencies in editable/local mode
console.log('Installing Python package dependencies...');
try {
  execSync(`"${pipPath}" install -e "${pkgDir}"`, { stdio: 'inherit', cwd: pkgDir });
  console.log('✅ Local Python environment successfully configured.');
} catch (err) {
  console.error('❌ Failed to install Python dependencies:', err.message);
  process.exit(1);
}

// Installation is deliberately side-effect free outside this package directory.
// Connecting AI clients changes their config files and therefore requires a
// separate, explicit command from the user.
console.log('No AI client configuration files were changed.');
console.log('Preview agent changes with: psyche connect --dry-run');
console.log('Connect detected agents with: psyche connect');
