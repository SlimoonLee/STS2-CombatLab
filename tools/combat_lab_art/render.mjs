import fs from 'node:fs';
import path from 'node:path';
import http from 'node:http';
import { fileURLToPath } from 'node:url';
import { chromium } from 'playwright';

const here = path.dirname(fileURLToPath(import.meta.url));
const [sourceArg, manifestArg, outputArg, browserArg] = process.argv.slice(2);
if (!sourceArg || !manifestArg || !outputArg) {
  throw new Error('Usage: node render.mjs SOURCE_DIR MANIFEST_JSON OUTPUT_DIR [BROWSER_EXE]');
}
const source = path.resolve(sourceArg);
const output = path.resolve(outputArg);
const manifest = JSON.parse(fs.readFileSync(manifestArg, 'utf8'));
fs.mkdirSync(output, { recursive: true });
const runtime = path.join(here, 'node_modules/@esotericsoftware/spine-webgl/dist/iife/spine-webgl.js');
const contentTypes = { '.js': 'text/javascript', '.html': 'text/html', '.png': 'image/png', '.atlas': 'text/plain', '.skel': 'application/octet-stream' };
const server = http.createServer((req, res) => {
  const urlPath = decodeURIComponent(new URL(req.url, 'http://localhost').pathname);
  let file;
  if (urlPath === '/spine.js') file = runtime;
  else if (urlPath.startsWith('/source/')) {
    file = path.resolve(source, urlPath.slice('/source/'.length));
    if (path.relative(source, file).startsWith('..')) file = null;
  } else if (['/', '/render.html', '/render-browser.js'].includes(urlPath)) {
    file = path.join(here, urlPath === '/' ? 'render.html' : urlPath.slice(1));
  }
  if (!file || !fs.existsSync(file)) {
    res.writeHead(404); res.end(); return;
  }
  res.setHeader('Content-Type', contentTypes[path.extname(file)] || 'application/octet-stream');
  fs.createReadStream(file).pipe(res);
});
await new Promise((resolve) => server.listen(0, '127.0.0.1', resolve));
let browser;
const rendered = {};
try {
  const executablePath = browserArg || (process.platform === 'win32'
    ? [process.env['ProgramFiles(x86)'], process.env.ProgramFiles]
      .filter(Boolean).map((root) => path.join(root, 'Microsoft/Edge/Application/msedge.exe'))
      .find((candidate) => fs.existsSync(candidate))
    : undefined);
  browser = await chromium.launch({
    headless: true,
    executablePath,
    args: ['--use-gl=angle', '--use-angle=swiftshader', '--enable-unsafe-swiftshader'],
  });
  const page = await browser.newPage({ viewport: { width: 768, height: 768 } });
  await page.goto(`http://127.0.0.1:${server.address().port}/`);
  for (const spec of manifest) {
    const result = await page.evaluate((value) => window.renderSprite(value), spec);
    const { png, ...details } = result;
    fs.writeFileSync(path.join(output, `${spec.id}.png`), Buffer.from(png.split(',')[1], 'base64'));
    rendered[spec.id] = { ...details, skeleton: spec.skeleton, atlas: spec.atlas };
    console.log(`Rendered ${spec.id}`);
  }
  fs.writeFileSync(path.join(output, 'poses.json'), JSON.stringify(rendered, null, 2));
  console.log(`Rendered ${manifest.length} static sprites.`);
} finally {
  await browser?.close();
  await new Promise((resolve) => server.close(resolve));
}
