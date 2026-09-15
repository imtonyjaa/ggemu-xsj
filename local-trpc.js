(function installLocalTrpcApi() {
  if (window.__miu2dLocalApiInstalled) return;
  window.__miu2dLocalApiInstalled = true;

  const originalFetch = window.fetch.bind(window);
  const DB_NAME = 'Miu2D_Saves';
  const STORE_NAME = 'saves';
  const localUser = {
    id: 'local_user',
    name: '本地玩家',
    email: 'local@player',
    emailVerified: true,
    role: 'USER',
    status: 'ACTIVE',
    avatarUrl: '',
    settings: {},
    createdAt: new Date().toISOString(),
    updatedAt: new Date().toISOString()
  };

  function initDB() {
    return new Promise((resolve, reject) => {
      const request = indexedDB.open(DB_NAME, 1);
      request.onupgradeneeded = (event) => {
        const db = event.target.result;
        if (!db.objectStoreNames.contains(STORE_NAME)) {
          db.createObjectStore(STORE_NAME, { keyPath: 'id' });
        }
      };
      request.onsuccess = () => resolve(request.result);
      request.onerror = () => reject(request.error);
    });
  }

  async function getDB() {
    if (!window.__miu2d_db) window.__miu2d_db = initDB();
    return window.__miu2d_db;
  }

  async function runStoreRequest(mode, operation) {
    const db = await getDB();
    return new Promise((resolve, reject) => {
      const tx = db.transaction(STORE_NAME, mode);
      const request = operation(tx.objectStore(STORE_NAME));
      request.onsuccess = () => resolve(request.result);
      request.onerror = () => reject(request.error);
    });
  }

  const getAllSaves = () => runStoreRequest('readonly', (store) => store.getAll());
  const getSave = (id) => runStoreRequest('readonly', (store) => store.get(id));
  const putSave = (save) => runStoreRequest('readwrite', (store) => store.put(save));
  const deleteSave = (id) => runStoreRequest('readwrite', (store) => store.delete(id));

  function unwrapInput(value) {
    return value?.json ?? value ?? {};
  }

  function createTrpcResponse(results, isBatch) {
    const payloads = results.map((result) => {
      if (result.handled) return { result: { data: result.data } };
      return {
        error: {
          json: {
            message: `Local API does not implement ${result.route}`,
            code: -32601,
            data: { code: 'NOT_FOUND', httpStatus: 404 }
          }
        }
      };
    });
    return new Response(JSON.stringify(isBatch ? payloads : payloads[0]), {
      status: 200,
      headers: { 'Content-Type': 'application/json' }
    });
  }

  async function handleRoute(route, input, method) {
    if (route === 'user.getProfile') return { handled: true, data: localUser };

    if (route === 'auth.login' && method === 'POST') {
      return {
        handled: true,
        data: { user: localUser, defaultGameSlug: 'demo' }
      };
    }

    if (route === 'save.list') {
      const allSaves = await getAllSaves();
      const gameSlug = input.gameSlug || 'demo';
      const saves = allSaves
        .filter((save) => save.gameSlug === gameSlug)
        .map(({ data, ...summary }) => summary);
      return { handled: true, data: saves };
    }

    if (route === 'save.get') {
      return { handled: true, data: (await getSave(input.saveId)) || null };
    }

    if (route === 'save.upsert' && method === 'POST') {
      const saveId = input.saveId || `save_${Date.now()}`;
      const existing = await getSave(saveId);
      const now = new Date().toISOString();
      const save = {
        id: saveId,
        gameSlug: input.gameSlug || 'demo',
        name: input.name || `存档 ${new Date().toLocaleString('zh-CN')}`,
        screenshot: input.screenshot,
        mapName: input.mapName,
        level: input.level,
        playerName: input.playerName,
        data: input.data,
        isShared: false,
        createdAt: existing?.createdAt || now,
        updatedAt: now
      };
      await putSave(save);
      return { handled: true, data: save };
    }

    if (route === 'save.delete' && method === 'POST') {
      await deleteSave(input.saveId);
      return { handled: true, data: { success: true } };
    }

    return { handled: false };
  }

  async function getRequestBody(resource, config) {
    if (config?.body) return config.body;
    if (resource instanceof Request && resource.body) return resource.clone().text();
    return '';
  }

  window.fetch = async function localFetch(resource, config) {
    const requestUrl = typeof resource === 'string'
      ? resource
      : resource instanceof Request
        ? resource.url
        : resource.toString();
    const url = new URL(requestUrl, window.location.origin);
    const trpcIndex = url.pathname.indexOf('/trpc/');
    if (trpcIndex === -1) return originalFetch(resource, config);

    const routes = url.pathname.slice(trpcIndex + 6).split(',');
    const isBatch = url.searchParams.get('batch') === '1';
    const method = (config?.method || (resource instanceof Request ? resource.method : 'GET')).toUpperCase();
    const encodedInput = method === 'GET' ? url.searchParams.get('input') : await getRequestBody(resource, config);

    let requestInputs = {};
    try {
      requestInputs = encodedInput ? JSON.parse(encodedInput) : {};
    } catch (error) {
      console.warn('Unable to parse local tRPC input.', error);
    }

    const results = await Promise.all(routes.map(async (route, index) => {
      const rawInput = isBatch ? requestInputs[index] : requestInputs;
      const result = await handleRoute(route, unwrapInput(rawInput), method);
      return { ...result, route };
    }));

    if (results.every((result) => !result.handled)) {
      return originalFetch(resource, config);
    }

    return createTrpcResponse(results, isBatch);
  };
})();
