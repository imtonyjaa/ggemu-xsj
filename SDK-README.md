# GGEMU HTML5 SDK 对接说明

本文档面向 HTML5 游戏接入方，说明如何把游戏接入 GGEMU SDK，并支持以下能力：

- 响应 GGEMU 注入的键盘事件
- 提供截图能力
- 提供录屏能力
- 接入直播推流
- 由宿主页面分配直播房间
- 查询、增加、使用宿主钱袋中的金币
- 记录游戏开始与结束并提交排行榜分数

当前示例代码见 [index.html](/Users/ezshine/Work/Projects/dashu.ai/GGEMU/web/apps/sdk-example/index.html)。

## 1. SDK 地址

开发环境：

```html
<script src="http://localhost:3000/api/ggemu-sdk.js"></script>
```

生产环境：

```html
<script src="https://ggemu.com/api/ggemu-sdk.js"></script>
```

SDK 加载后会在 `window` 上挂载全局对象：

```js
window.GGEMU
```

## 2. 最小接入流程

最小可用流程如下：

```html
<canvas id="game-canvas" width="1280" height="720"></canvas>
<script src="https://ggemu.com/api/ggemu-sdk.js"></script>
<script>
  const canvas = document.getElementById('game-canvas');
  const audioContext = new AudioContext();
  const masterGain = audioContext.createGain();
  masterGain.connect(audioContext.destination);

  GGEMU.init({
    debug: false,
    parentOrigin: '*',
  });

  GGEMU.setInputHandler((input) => {
    // input.action: keydown | keyup
    // input.key:    例如 "w" / "Enter" / "ArrowLeft"
    // input.code:   例如 "KeyW" / "Enter" / "ArrowLeft"
    handleGameInput(input);
    return true;
  });

  GGEMU.registerCanvas(canvas);
  GGEMU.registerAudioNode(masterGain);

  GGEMU.setReady({
    game: 'your-game-name',
  });
</script>
```

建议顺序：

1. 先加载 SDK
2. 调用 `GGEMU.init()`
3. 注册输入处理 `GGEMU.setInputHandler()`
4. 注册画面源 `GGEMU.registerCanvas()`
5. 注册音频源 `GGEMU.registerAudioNode()` 或 `GGEMU.registerAudioStream()`
6. 调用 `GGEMU.setReady()`

## 3. 键盘输入对接

GGEMU 会把宿主页面、体感控制器、或其他桥接输入统一转换成输入事件，再交给游戏。

推荐接入方式：

```js
GGEMU.setInputHandler((input) => {
  const inputId = input.code || input.key;

  if (input.action !== 'keydown') {
    return true;
  }

  switch (inputId) {
    case 'KeyW':
      moveUp();
      break;
    case 'KeyA':
      moveLeft();
      break;
    case 'KeyS':
      moveDown();
      break;
    case 'KeyD':
      moveRight();
      break;
    case 'KeyJ':
      actionJ();
      break;
    case 'KeyK':
      actionK();
      break;
    case 'Enter':
      confirm();
      break;
  }

  return true;
});
```

输入对象结构：

```js
{
  action: 'keydown' | 'keyup',
  key: 'w',
  code: 'KeyW',
  repeat: false,
  source: 'host' | 'body-controller' | 'native-keyboard' | '...',
  timestamp: 1710000000000
}
```

说明：

- 推荐优先使用 `input.code` 判断按键
- `source` 可用于区分输入来源
- 如果未调用 `setInputHandler()`，SDK 会尝试派发浏览器原生键盘事件作为兜底

## 4. 截图能力

截图能力依赖 `registerCanvas(canvas)`。

注册：

```js
GGEMU.registerCanvas(canvas);
```

主动截图：

```js
const result = await GGEMU.captureScreenshot({
  mimeType: 'image/jpeg',
  quality: 0.92,
});

console.log(result);
```

返回结构：

```js
{
  blob: Blob,
  mimeType: 'image/jpeg',
  width: 1280,
  height: 720
}
```

说明：

- 如果没有注册 canvas，截图会失败
- `mimeType` 默认是 `image/jpeg`
- `quality` 取值范围是 `0` 到 `1`

## 5. 录制能力

录制能力依赖视频源，通常来自 `registerCanvas(canvas)`；音频可以来自：

- `registerAudioNode(audioNode)`
- `registerAudioStream(audioStream)`

开始录制：

```js
await GGEMU.startRecording({
  maxDuration: 15,
});
```

停止录制：

```js
const result = await GGEMU.stopRecording();

if (result) {
  console.log(result.blob, result.mimeType);
}
```

返回结构：

```js
{
  blob: Blob,
  mimeType: 'video/webm',
  startedAt: 1710000000000,
  endedAt: 1710000015000
}
```

说明：

- 录制依赖浏览器支持 `MediaRecorder`
- `maxDuration` 默认按秒倒计时
- 到时后 SDK 会自动结束录制
- 自动结束后，SDK 也会向宿主发送录制结果

可监听事件：

```js
GGEMU.on('ggemu:recording-started', onStarted);
GGEMU.on('ggemu:recording-progress', onProgress);
GGEMU.on('ggemu:recording-stopped', onStopped);
```

## 6. 直播房间与推流

直播分两部分：

1. 申请直播房间信息
2. 使用分配到的 `streamName` 发起 WebRTC 推流

### 6.1 最推荐方式

如果游戏运行在 GGEMU 宿主 iframe 中，推荐直接这样：

```js
await GGEMU.startLive();
```

SDK 会自动：

- 向宿主请求直播房间
- 等待宿主返回 `roomId` / `streamName`
- 自动发起直播推流

前提：

- 已注册视频源 `registerCanvas()` 或 `registerCaptureStream()`
- 已提供可选音频源 `registerAudioNode()` 或 `registerAudioStream()`
- 页面运行在 GGEMU 宿主环境中

### 6.2 显式申请直播房间

如果你想先拿到房间信息，再决定什么时候推流：

```js
await GGEMU.requestLiveRoom({
  gameId: 'your-game-id',
});

await GGEMU.startLive();
```

### 6.3 手动配置房间信息

如果不是由宿主分配，也可以自己直接传入：

```js
GGEMU.init({
  streamName: 'your-stream-name',
  roomId: 'your-room-id',
  parentOrigin: '*',
});

await GGEMU.startLive();
```

### 6.4 停止直播

```js
GGEMU.stopLive();
```

可监听事件：

```js
GGEMU.on('ggemu:live-room-ready', (state) => {
  console.log('room ready', state.config.roomId, state.config.streamName);
});

GGEMU.on('ggemu:live-started', (state) => {
  console.log('live started', state);
});

GGEMU.on('ggemu:live-stopped', (state) => {
  console.log('live stopped', state);
});
```

## 7. 画面与音频源注册方式

### 7.1 注册 Canvas

适合大多数纯 Canvas HTML5 游戏：

```js
GGEMU.registerCanvas(canvas);
```

### 7.2 注册 Web Audio 节点

适合游戏音频走 Web Audio 混音总线：

```js
GGEMU.registerAudioNode(masterGain);
```

要求：

- 传入对象必须支持 `connect()`
- 且对象来自 `AudioContext`

### 7.3 注册音频流

如果你的音频本身已经是 `MediaStream`：

```js
GGEMU.registerAudioStream(audioStream);
```

### 7.4 注册自定义采集流

如果你不想直接从 canvas 采集，而是自己提供组合后的视频流：

```js
GGEMU.registerCaptureStream(() => {
  return {
    stream: customStream,
    stop() {
      customStream.getTracks().forEach((track) => track.stop());
    },
  };
});
```

适用场景：

- 多 canvas 合成
- WebGL 离屏渲染
- 自定义视频管线

## 8. 常用事件

推荐至少监听这些事件：

```js
GGEMU.on('ggemu:sdk-loaded', (state) => console.log('sdk loaded', state));
GGEMU.on('ggemu:sdk-ready', (state) => console.log('sdk ready', state));
GGEMU.on('ggemu:ready', (state) => console.log('game ready', state));
GGEMU.on('ggemu:status', (state) => console.log('status', state.lastStatus));
GGEMU.on('ggemu:error', (state) => console.error('sdk error', state.lastError));
GGEMU.on('ggemu:input', (payload) => console.log('input', payload.input));
```

常见事件说明：

- `ggemu:sdk-loaded`：SDK 脚本已加载
- `ggemu:sdk-ready`：`init()` 后 SDK 已准备好
- `ggemu:ready`：游戏调用 `setReady()` 后触发
- `ggemu:status`：状态更新
- `ggemu:error`：发生错误
- `ggemu:input`：收到输入事件
- `ggemu:live-room-ready`：房间信息已就绪
- `ggemu:live-started`：直播已开始
- `ggemu:live-stopped`：直播已停止
- `ggemu:recording-started`：录制已开始
- `ggemu:recording-progress`：录制倒计时变化
- `ggemu:recording-stopped`：录制已停止
- `ggemu:game-start-logged`：游戏开始日志已记录
- `ggemu:game-finish-logged`：游戏结束日志已提交

## 9. 常用 API 一览

```js
GGEMU.init(config)
GGEMU.configure(config)
GGEMU.registerCanvas(canvas)
GGEMU.registerAudioNode(audioNode)
GGEMU.registerAudioStream(audioStream)
GGEMU.registerCaptureStream(streamOrFactory)
GGEMU.setInputHandler(handler)
GGEMU.setStatus(message, progress, extra)
GGEMU.setReady(extra)
GGEMU.getState()
GGEMU.logGameStart()
GGEMU.logGameFinish(scoreData, options?)
GGEMU.requestLiveRoom(config?)
GGEMU.startLive(config?)
GGEMU.stopLive()
GGEMU.captureScreenshot(options?)
GGEMU.startRecording(options?)
GGEMU.stopRecording()
GGEMU.on(eventName, handler)
GGEMU.off(eventName, handler?)
GGEMU.destroy()
```

## 10. 推荐接入模板

下面是一段适合 HTML5 游戏接入的推荐模板：

```js
async function bootGgemu(canvas, masterGain) {
  GGEMU.init({
    debug: false,
    parentOrigin: '*',
  });

  GGEMU.on('ggemu:error', (state) => {
    console.error('[GGEMU]', state.lastError);
  });

  GGEMU.setInputHandler((input) => {
    handleGameInput(input);
    return true;
  });

  GGEMU.registerCanvas(canvas);

  if (masterGain) {
    GGEMU.registerAudioNode(masterGain);
  }

  GGEMU.setReady({
    engine: 'custom-html5',
  });
}
```

## 11. 常见问题

### 11.1 为什么截图失败？

通常是因为没有先调用：

```js
GGEMU.registerCanvas(canvas);
```

### 11.2 为什么直播开始失败？

常见原因：

- 没有视频源
- 没有 `streamName`
- 没有运行在宿主环境中，且也没有手动配置房间信息
- SRS 相关配置不可用

### 11.3 为什么没有收到输入？

先检查：

- 是否调用了 `GGEMU.setInputHandler()`
- 是否按 `input.code` 而不是 `input.key` 做判断
- 游戏是否把自己的输入系统完全锁死，导致没有消费 SDK 输入

### 11.4 游戏不是 canvas，能不能接？

可以。你可以用：

```js
GGEMU.registerCaptureStream(customStream);
```

自己提供采集后的 `MediaStream`。

## 12. 参考示例

完整示例见：

- [index.html](/Users/ezshine/Work/Projects/dashu.ai/GGEMU/web/apps/sdk-example/index.html)

这个示例里已经演示了：

- SDK 加载
- 输入处理
- canvas 注册
- 音频注册
- `setReady()`
- `startLive()`
- `logGameStart()`
- `logGameFinish()`
- `getBagStatus()`
- `addBagCoins()`
- `useBagCoins()`

## 13. 游戏日志与排行榜

游戏开始时调用：

```js
await GGEMU.logGameStart();
```

游戏结束时提交分数：

```js
await GGEMU.logGameFinish(
  {
    score: 9200,
    coins: 42
  },
  {
    levelId: 'level-1'
  }
);
```

如果不传 `levelId`，默认使用 `default`。`scoreData` 必须是扁平对象，并且必须包含有限数字 `score` 字段；排行榜 best score 只按 `score` 判断。

说明：

- `logGameStart()` 不需要参数
- `logGameFinish(scoreData, options?)` 会把分数交给 GGEMU 宿主页面，由宿主页面在提交前完成封包
- 游戏运行在 GGEMU 宿主 iframe 中时才可提交
- 提交分数需要用户已登录；未登录时宿主页面会提示登录

## 14. 钱袋金币

钱袋金币能力依赖 GGEMU 宿主页面桥接。游戏运行在 GGEMU 宿主 iframe 中时，可以直接调用：

```js
const bagStatus = await GGEMU.getBagStatus();
const addResult = await GGEMU.addBagCoins(3);
const useResult = await GGEMU.useBagCoins(2);
```

返回结果会带上最新钱袋状态，例如：

```js
{
  success: true,
  bag_count: 8,
  bag_max: 999,
  can_pickup: true,
  can_claim: false
}
```

说明：

- `addBagCoins(amount)` 会尽量加入指定数量，直到钱袋达到上限
- `useBagCoins(amount)` 会扣减指定数量；如果余额不足会返回错误
- 钱袋指令的传输编码由 GGEMU 宿主页桥接层内部处理，SDK 接入方不需要自行构造底层请求参数
- 每次状态变化后，SDK 会触发 `ggemu:bag-updated`
