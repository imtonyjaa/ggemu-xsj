const http = require('http');
const fs = require('fs');
const path = require('path');
const { parse } = require('querystring');

const PORT = 8080;
const PUBLIC_DIR = __dirname;
const SAVES_FILE = path.join(__dirname, 'saves.json');

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

// 简单的持久化存取
function loadSaves() {
  if (fs.existsSync(SAVES_FILE)) {
    try {
      return JSON.parse(fs.readFileSync(SAVES_FILE, 'utf-8'));
    } catch (e) {
      return {};
    }
  }
  return {};
}

function saveSaves(data) {
  fs.writeFileSync(SAVES_FILE, JSON.stringify(data, null, 2));
}

// 帮助函数：返回 tRPC 成功格式
function trpcSuccess(res, data) {
  res.writeHead(200, { 'Content-Type': 'application/json' });
  res.end(JSON.stringify({ result: { data } }));
}

function parseJsonBody(req) {
  return new Promise((resolve) => {
    let body = '';
    req.on('data', chunk => {
      body += chunk.toString();
    });
    req.on('end', () => {
      try {
        resolve(JSON.parse(body));
      } catch (e) {
        resolve({});
      }
    });
  });
}

const server = http.createServer(async (req, res) => {
  // CORS & No-Cache
  res.setHeader('Access-Control-Allow-Origin', '*');
  res.setHeader('Access-Control-Allow-Headers', 'content-type, authorization');
  res.setHeader('Cache-Control', 'no-store, no-cache, must-revalidate, proxy-revalidate');
  res.setHeader('Pragma', 'no-cache');
  res.setHeader('Expires', '0');

  if (req.method === 'OPTIONS') {
    res.writeHead(200);
    res.end();
    return;
  }

  const urlObj = new URL(req.url, `http://${req.headers.host}`);
  const urlPath = urlObj.pathname;
  const isBatch = urlObj.searchParams.get('batch') === '1';

  // 帮助函数：返回 tRPC 成功格式，适配 batch=1
  function trpcSuccess(res, data) {
    res.writeHead(200, { 'Content-Type': 'application/json' });
    const payload = { result: { data } };
    res.end(JSON.stringify(isBatch ? [payload] : payload));
  }

  // --- tRPC 拦截（去掉登录弹窗与本地存档接入） ---
  const trpcIndex = urlPath.indexOf('/trpc/');
  if (trpcIndex !== -1) {
    let route = urlPath.substring(trpcIndex + 6);
    // TRPC 可能把 route 变成带有逗号的列表，这里我们取主路由
    if (route.includes(',')) {
      route = route.split(',')[0];
    }

    if (route === 'user.getProfile') {
      // 模拟登录态，去掉登录弹窗
      return trpcSuccess(res, {
        id: "local_user",
        name: "本地玩家",
        email: "local@player",
        emailVerified: true,
        role: "USER",
        status: "ACTIVE",
        avatarUrl: "",
        settings: {},
        createdAt: new Date().toISOString(),
        updatedAt: new Date().toISOString()
      });
    }

    if (route === 'auth.login' && req.method === 'POST') {
      return trpcSuccess(res, {
        user: {
          id: "local_user",
          name: "本地玩家",
          email: "local@player",
          emailVerified: true,
          role: "USER",
          status: "ACTIVE",
          avatarUrl: "",
          settings: {},
          createdAt: new Date().toISOString(),
          updatedAt: new Date().toISOString()
        },
        defaultGameSlug: "demo"
      });
    }

    if (route === 'auth.logout' && req.method === 'POST') {
      return trpcSuccess(res, { success: true });
    }

    if (route === 'save.list') {
      const inputStr = urlObj.searchParams.get('input');
      let gameSlug = 'demo';
      try {
        const inputObj = JSON.parse(inputStr);
        gameSlug = (isBatch ? inputObj['0'].gameSlug : inputObj['0'].json.gameSlug) || gameSlug;
      } catch(e) {}
      
      const db = loadSaves();
      const allSaves = Object.values(db).filter(s => s.gameSlug === gameSlug);
      // tRPC 期望一个列表，包含元数据
      const list = allSaves.map(s => {
        // 排除 data，减小体积
        const { data, ...rest } = s;
        return rest;
      });
      return trpcSuccess(res, list);
    }

    if (route === 'save.get') {
      const inputStr = urlObj.searchParams.get('input');
      let saveId = '';
      try {
        const inputObj = JSON.parse(inputStr);
        saveId = isBatch ? inputObj['0'].saveId : inputObj['0'].json.saveId;
      } catch(e) {}
      
      const db = loadSaves();
      return trpcSuccess(res, db[saveId] || null);
    }

    if (route === 'save.upsert' && req.method === 'POST') {
      const body = await parseJsonBody(req);
      const input = (isBatch ? body['0'] : body['0']?.json) || {};
      
      const db = loadSaves();
      const saveId = input.saveId || `save_${Date.now()}`;
      
      const newSave = {
        id: saveId,
        gameSlug: input.gameSlug || 'demo',
        name: input.name || `存档 ${new Date().toLocaleString('zh-CN')}`,
        screenshot: input.screenshot,
        mapName: input.mapName,
        level: input.level,
        playerName: input.playerName,
        data: input.data,
        isShared: false,
        createdAt: input.saveId ? (db[saveId]?.createdAt || new Date().toISOString()) : new Date().toISOString(),
        updatedAt: new Date().toISOString()
      };
      
      db[saveId] = newSave;
      saveSaves(db);
      
      return trpcSuccess(res, newSave);
    }

    if (route === 'save.delete' && req.method === 'POST') {
      const body = await parseJsonBody(req);
      const input = (isBatch ? body['0'] : body['0']?.json) || {};
      const saveId = input.saveId;
      
      const db = loadSaves();
      if (db[saveId]) {
        delete db[saveId];
        saveSaves(db);
      }
      return trpcSuccess(res, { success: true });
    }

    if (route === 'save.share' && req.method === 'POST') {
      const body = await parseJsonBody(req);
      const input = (isBatch ? body['0'] : body['0']?.json) || {};
      const saveId = input.saveId;
      
      const db = loadSaves();
      if (db[saveId]) {
        db[saveId].isShared = input.isShared;
        db[saveId].shareCode = input.isShared ? 'local_share' : undefined;
        saveSaves(db);
        return trpcSuccess(res, db[saveId]);
      }
      return trpcSuccess(res, { success: false });
    }

    // 默认空响应或 404
    res.writeHead(404, { 'Content-Type': 'application/json' });
    return res.end(JSON.stringify({ error: "tRPC route not found" }));
  }
  // --- 拦截结束 ---

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
    // 区分前端页面路由和静态资源请求
    const ext = path.extname(urlPath);
    const isResource = (ext && ext !== '.html') || 
                       urlPath.includes('/resources/') || 
                       urlPath.includes('/assets/') || 
                       urlPath.includes('/api/');
                       
    if (!isResource) {
      const indexPath = path.join(PUBLIC_DIR, 'index.html');
      if (fs.existsSync(indexPath)) {
        res.setHeader('Content-Type', 'text/html; charset=utf-8');
        fs.createReadStream(indexPath).pipe(res);
        return;
      }
    }
    
    // 针对不存在的资源请求，返回 404 JSON
    res.writeHead(404, { 'Content-Type': 'application/json' });
    res.end(JSON.stringify({ error: "File not found" }));
  }
});

server.listen(PORT, () => {
  console.log(`本地服务器已启动，请访问: http://127.0.0.1:${PORT}/game/demo`);
});
