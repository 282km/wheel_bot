(function () {
  const tg = window.Telegram && window.Telegram.WebApp;
  if (tg) {
    tg.ready();
    tg.expand();
    if (typeof tg.setHeaderColor === "function") {
      tg.setHeaderColor("#0f1419");
    }
    if (typeof tg.setBackgroundColor === "function") {
      tg.setBackgroundColor("#0f1419");
    }
  }

  const titleEl = document.getElementById("title");
  const statusEl = document.getElementById("status");
  const videoEl = document.getElementById("video");
  const offlineEl = document.getElementById("offline");

  function setStatus(text, kind) {
    statusEl.textContent = text;
    statusEl.className = "status" + (kind ? " status-" + kind : "");
  }

  function showOffline() {
    offlineEl.classList.remove("hidden");
    videoEl.classList.add("hidden");
    setStatus("Эфир не идёт", "error");
  }

  function attachStream(hlsUrl) {
    if (window.Hls && Hls.isSupported()) {
      const hls = new Hls({
        lowLatencyMode: true,
        backBufferLength: 30,
      });
      hls.loadSource(hlsUrl);
      hls.attachMedia(videoEl);
      hls.on(Hls.Events.MANIFEST_PARSED, function () {
        setStatus("В эфире", "live");
        offlineEl.classList.add("hidden");
        videoEl.classList.remove("hidden");
        videoEl.play().catch(function () {
          setStatus("Нажмите play на видео", "");
        });
      });
      hls.on(Hls.Events.ERROR, function (_event, data) {
        if (data.fatal) {
          showOffline();
        }
      });
      return;
    }

    if (videoEl.canPlayType("application/vnd.apple.mpegurl")) {
      videoEl.src = hlsUrl;
      videoEl.addEventListener("loadedmetadata", function () {
        setStatus("В эфире", "live");
        offlineEl.classList.add("hidden");
        videoEl.classList.remove("hidden");
        videoEl.play().catch(function () {
          setStatus("Нажмите play на видео", "");
        });
      });
      videoEl.addEventListener("error", showOffline);
      return;
    }

    setStatus("Браузер не поддерживает HLS", "error");
    showOffline();
  }

  fetch("/api/live/config")
    .then(function (resp) {
      return resp.json();
    })
    .then(function (cfg) {
      if (!cfg.enabled) {
        titleEl.textContent = "Трансляция";
        showOffline();
        return;
      }
      titleEl.textContent = cfg.title || "Трансляция";
      return fetch("/api/live/status")
        .then(function (resp) {
          return resp.json();
        })
        .then(function (status) {
          if (!status.live) {
            showOffline();
            return;
          }
          attachStream(cfg.hlsUrl);
        });
    })
    .catch(function () {
      setStatus("Не удалось загрузить настройки", "error");
      showOffline();
    });
})();
