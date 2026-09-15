(function installLocalUiOverrides() {
  if (window.__miu2dLocalUiOverridesInstalled) return;
  window.__miu2dLocalUiOverridesInstalled = true;

  const FEEDBACK_LABEL = '反馈Bug';
  const CHEAT_LABEL = '作弊器';
  const DEBUG_BUTTON_TITLE = '调试';
  const DEBUG_PANEL_TITLE = '调试面板';
  const GAME_DEBUG_TITLE = '游戏调试';
  const CHEAT_BUTTON_ATTRIBUTE = 'data-local-cheat-button';

  function findButtonByText(text) {
    return Array.from(document.querySelectorAll('button')).find(
      (button) => button.textContent.trim() === text
    );
  }

  function findButtonContainingText(text) {
    return Array.from(document.querySelectorAll('button')).find(
      (button) => button.textContent.includes(text)
    );
  }

  function markCheatButton() {
    const button = findButtonByText(FEEDBACK_LABEL);
    if (!button) return;

    button.textContent = CHEAT_LABEL;
    button.setAttribute(CHEAT_BUTTON_ATTRIBUTE, 'true');
  }

  function findDebugPanel() {
    return Array.from(document.querySelectorAll('h2')).find(
      (heading) => heading.textContent.trim() === DEBUG_PANEL_TITLE
    );
  }

  function openGameDebugSection(attempt = 0) {
    const button = findButtonContainingText(GAME_DEBUG_TITLE);
    if (!button) {
      if (attempt < 40) {
        window.setTimeout(() => openGameDebugSection(attempt + 1), 50);
      }
      return;
    }

    if (!button.nextElementSibling) button.click();
    button.scrollIntoView({ block: 'start' });
  }

  function closeSettingsMenu() {
    window.dispatchEvent(new KeyboardEvent('keydown', {
      key: 'Escape',
      code: 'Escape',
      bubbles: true,
      cancelable: true
    }));
  }

  function findReactFiber(element) {
    const fiberKey = Object.keys(element).find((key) => key.startsWith('__reactFiber$'));
    return fiberKey ? element[fiberKey] : null;
  }

  function findDebugActionInFiber(fiber) {
    let hook = fiber?.memoizedState;
    let inspectedHooks = 0;

    while (hook && inspectedHooks < 100) {
      const state = hook.memoizedState;
      if (Array.isArray(state)) {
        const debugAction = state.find(
          (item) => item?.id === 'debug' && typeof item.onClick === 'function'
        );
        if (debugAction) return debugAction.onClick;
      }

      hook = hook.next;
      inspectedHooks += 1;
    }

    return null;
  }

  function findDebugAction() {
    const gameContainer = document.querySelector('[role="application"]');
    let fiber = gameContainer ? findReactFiber(gameContainer) : null;

    while (fiber) {
      const debugAction = findDebugActionInFiber(fiber)
        || findDebugActionInFiber(fiber.alternate);
      if (debugAction) return debugAction;

      fiber = fiber.return;
    }

    return null;
  }

  function triggerDebugPanel() {
    const debugButton = document.querySelector(`button[title="${DEBUG_BUTTON_TITLE}"]`);
    if (debugButton) {
      debugButton.click();
      return true;
    }

    const debugAction = findDebugAction();
    if (!debugAction) return false;

    debugAction();
    return true;
  }

  function openDebugPanel() {
    if (findDebugPanel()) {
      openGameDebugSection();
      return;
    }

    if (!triggerDebugPanel()) {
      console.warn('Unable to find the debug panel action.');
      return;
    }

    openGameDebugSection();
  }

  function openCheatPanel() {
    closeSettingsMenu();
    window.setTimeout(openDebugPanel, 0);
  }

  document.addEventListener('click', (event) => {
    const target = event.target;
    if (!(target instanceof Element)) return;

    const button = target.closest(`button[${CHEAT_BUTTON_ATTRIBUTE}="true"]`);
    if (!button) return;

    event.preventDefault();
    event.stopPropagation();
    event.stopImmediatePropagation();
    openCheatPanel();
  }, true);

  const observer = new MutationObserver(markCheatButton);
  observer.observe(document.documentElement, {
    childList: true,
    subtree: true,
    characterData: true
  });

  markCheatButton();
})();
