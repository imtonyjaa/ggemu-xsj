const http = require('http');
const fs = require('fs');
const path = require('path');

const PORT = 8080;
const PUBLIC_DIR = __dirname;

const mimeTypes = {
  '.html': 'text/html; charset=utf-8',
  '.js': 'text/javascript',
  '.css': 'text/css',
  '.json': 'application/json',
  '.bin': 'application/octet-stream',
  '.png': 'image/png',
  '.jpg': 'image/jpeg',
  '.svg': 'image/svg+xml',
  '.wasm': 'application/wasm'
};

const server = http.createServer((req, res) => {
  // CORS
  res.setHeader('Access-Control-Allow-Origin', '*');
  res.setHeader('Access-Control-Allow-Headers', 'content-type, authorization');
  if (req.method === 'OPTIONS') {
    res.writeHead(200);
    res.end();
    return;
  }

  // --- 纯净版：无任何 tRPC 拦截，恢复到最初的状态 ---
  const urlPath = req.url.split('?')[0];

  const possiblePaths = [
    urlPath,
    urlPath + '.html',
    urlPath + '.bin',
    urlPath + '.json',
    path.join(urlPath, 'index.html')
  ];

  let foundPath = null;
  for (let p of possiblePaths) {
    const fullPath = path.join(PUBLIC_DIR, p);
    if (!fullPath.startsWith(PUBLIC_DIR)) continue;

    if (fs.existsSync(fullPath) && fs.statSync(fullPath).isFile()) {
      foundPath = fullPath;
      break;
    }
  }

  if (foundPath) {
    const ext = path.extname(foundPath);
    res.setHeader('Content-Type', mimeTypes[ext] || 'application/octet-stream');
    fs.createReadStream(foundPath).pipe(res);
  } else {
    const indexPath = path.join(PUBLIC_DIR, 'index.html');
    if (fs.existsSync(indexPath)) {
      res.setHeader('Content-Type', 'text/html; charset=utf-8');
      fs.createReadStream(indexPath).pipe(res);
    } else {
      res.writeHead(404);
      res.end('Not found');
    }
  }
});

server.listen(PORT, () => {
  console.log(`本地纯净版服务器已启动，请访问: http://localhost:${PORT}`);
});
