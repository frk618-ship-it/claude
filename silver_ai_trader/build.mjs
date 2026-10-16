// 建置 silver-ai-trader.html
//
//   npm install        （第一次）
//   npm run build      產生 silver-ai-trader.html
//   npm run check      確認 silver-ai-trader.html 與 src/ 同步（不寫檔，不同步就回傳非 0）
//
// 做的事：
//   1. src/app.jsx（含共用的 src/core.js）在建置時就打包成一般 JS —— 瀏覽器不用再下載 Babel、每次開頁面現場編譯
//   2. React / ReactDOM 打包內嵌，不再依賴 unpkg
//   3. 掃描原始碼產生 Tailwind CSS 並內嵌，取代執行期的 cdn.tailwindcss.com
//   4. 只抽出用到的 Lucide 圖示 SVG 資料內嵌
//   5. TF.js / Plotly 仍走 CDN（體積大、可被瀏覽器快取），版本取自 node_modules，
//      並附上由同一份檔案算出的 SRI 雜湊
import { readFileSync, writeFileSync } from 'node:fs';
import { createHash } from 'node:crypto';
import { createRequire } from 'node:module';
import { fileURLToPath } from 'node:url';
import path from 'node:path';
import * as esbuild from 'esbuild';
import postcss from 'postcss';
import tailwindcss from 'tailwindcss';

const require = createRequire(import.meta.url);
const ROOT = path.dirname(fileURLToPath(import.meta.url));
const NM = path.join(ROOT, 'node_modules');
const OUT = path.join(ROOT, 'silver-ai-trader.html');
const read = (p) => readFileSync(path.join(ROOT, p), 'utf8');
const checkOnly = process.argv.includes('--check');

const appSrc = read('src/app.jsx');
// 版本號只在 package.json 維護一處，建置時注入頁面標題與 App（APP_VERSION）
const { version } = JSON.parse(read('package.json'));
const appVersion = 'v' + version.split('.').slice(0, 2).join('.');
const template = read('src/template.html');

// ── 1. App：只轉 JSX，不打包（程式碼依賴 window 上的 React / tf / Plotly 全域變數）──
// app.jsx 以 import 引用 src/core.js（與雲端訓練共用的核心），打包成單一 IIFE。
// React / tf / Plotly 仍取自 window 全域變數，不打包進來。
const app = (await esbuild.build({
    entryPoints: [path.join(ROOT, 'src/app.jsx')],
    bundle: true, format: 'iife', target: 'es2020', charset: 'utf8', write: false,
    loader: { '.jsx': 'jsx' },
    define: { APP_VERSION: JSON.stringify(appVersion) },
})).outputFiles[0].text;

// ── 2. React 打包 ──
const vendor = (await esbuild.build({
    stdin: {
        contents: `import React from 'react';
                   import * as ReactDOM from 'react-dom/client';
                   window.React = React;
                   window.ReactDOM = ReactDOM;`,
        resolveDir: ROOT,
    },
    bundle: true, minify: true, format: 'iife', target: 'es2020', write: false,
    define: { 'process.env.NODE_ENV': '"production"' },
})).outputFiles[0].text;

// ── 3. Lucide 圖示 ──
// 來源：<Icon name="x">、<Icon name={cond ? 'a' : 'b'}>、以及資料中的 icon: 'x'
const lucide = require('lucide');
const toPascal = (k) => k.replace(/(^|-)([a-z0-9])/g, (_, __, c) => c.toUpperCase());
const iconNames = new Set();
for (const m of appSrc.matchAll(/<Icon\s+name=(?:"([^"]+)"|\{([^}]*)\})/g)) {
    if (m[1]) iconNames.add(m[1]);
    else for (const q of m[2].matchAll(/'([a-z0-9-]+)'/g)) iconNames.add(q[1]);
}
for (const m of appSrc.matchAll(/\bicon:\s*'([a-z0-9-]+)'/g)) iconNames.add(m[1]);
const icons = {};
const missing = [];
for (const name of [...iconNames].sort()) {
    const node = lucide.icons[toPascal(name)];
    if (!node) { missing.push(name); continue; }
    icons[name] = node[2];   // [tag, attrs, children] → 只取子節點
}
if (missing.length) {
    console.error(`找不到這些 Lucide 圖示：${missing.join(', ')}`);
    process.exit(1);
}
const iconsJs = `window.LUCIDE_ICONS=${JSON.stringify(icons)};`;

// ── 4. Tailwind CSS（預設設定，與原本 cdn.tailwindcss.com 相同）──
const twResult = await postcss([tailwindcss({
    content: [
        { raw: appSrc, extension: 'jsx' },
        { raw: read('src/core.js'), extension: 'js' },
        { raw: template, extension: 'html' },
    ],
})]).process('@tailwind base;\n@tailwind components;\n@tailwind utilities;', { from: undefined });
const css = (await esbuild.transform(twResult.css, { loader: 'css', minify: true })).code;

// ── 5. 套入範本 ──
const pkgVersion = (name) => require(path.join(NM, name, 'package.json')).version;
const sri = (file) => 'sha384-' + createHash('sha384').update(readFileSync(path.join(NM, file))).digest('base64');
// 內嵌腳本裡的 "</script" 會提早結束 <script> 標籤
const inlineJs = (code) => `<script>\n${code.replace(/<\/script/gi, '<\\/script')}\n</script>`;

let html = template
    .replace(/\{\{appVersion\}\}/g, () => appVersion)
    .replace(/\{\{version:([^}]+)\}\}/g, (_, name) => pkgVersion(name))
    .replace(/\{\{sri:([^}]+)\}\}/g, (_, file) => sri(file));
// 用函式當替換值：程式碼裡的 "$&"、"$1" 等字樣不能被當成替換樣式
html = html.replace('<!-- @TAILWIND_CSS -->', () => `<style>${css}</style>`);
html = html.replace('    <!-- @APP_JS -->', () => [inlineJs(vendor), inlineJs(iconsJs), inlineJs(app)].join('\n'));
if (html.includes('<!-- @') || html.includes('{{')) {
    console.error('範本中仍有未替換的佔位符');
    process.exit(1);
}

if (checkOnly) {
    let current = '';
    try { current = readFileSync(OUT, 'utf8'); } catch (_) {}
    if (current !== html) {
        console.error('silver-ai-trader.html 與 src/ 不同步，請執行 npm run build');
        process.exit(1);
    }
    console.log('silver-ai-trader.html 已與 src/ 同步');
} else {
    writeFileSync(OUT, html);
    const kb = (s) => (Buffer.byteLength(s) / 1024).toFixed(0) + ' KB';
    console.log(`已輸出 ${path.relative(ROOT, OUT)}（${kb(html)}）：app ${kb(app)}、React ${kb(vendor)}、CSS ${kb(css)}、圖示 ${Object.keys(icons).length} 個`);
}
