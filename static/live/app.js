(function () {
  const tg = window.Telegram && window.Telegram.WebApp;
  const titleEl = document.getElementById("title");
  const statusEl = document.getElementById("status");
  const videoEl = document.getElementById("video");
  const offlineEl = document.getElementById("offline");
  const fsBtn = document.getElementById("fs-btn");

  if (tg) {
    document.body.classList.add("webapp");
    tg.ready();
    tg.expand();
    if (typeof tg.disableVerticalSwipes === "function") {
      tg.disableVerticalSwipes();
    }
    if (typeof tg.setHeaderColor === "function") {
      tg.setHeaderColor("#0f1419");
    }
    if (typeof tg.setBackgroundColor === "function") {
      tg.setBackgroundColor("#0f1419");
    }
    if (typeof tg.isVersionAtLeast === "function" && tg.isVersionAtLeast("8.0")) {
      try {
        tg.requestFullscreen();
      } catch (_e) {
        /* older clients */
      }
    }
  }

  function setStatus(text, kind) {
    statusEl.textContent = text;
    statusEl.className = "status" + (kind ? " status-" + kind : "");
  }

  function showOffline(message) {
    document.body.classList.remove("playing");
    offlineEl.classList.remove("hidden");
    videoEl.classList.add("hidden");
    fsBtn.classList.add("hidden");
    setStatus(message || "Эфир не идёт", "error");
  }

  function onPlaying() {
    document.body.classList.add("playing");
    offlineEl.classList.add("hidden");
    videoEl.classList.remove("hidden");
    fsBtn.classList.remove("hidden");
    setStatus("В эфире", "live");
  }

  function enterFullscreen() {
    if (tg && typeof tg.requestFullscreen === "function") {
      try {
        tg.requestFullscreen();
        return;
      } catch (_e) {
        /* fall through */
      }
    }
    if (videoEl.webkitEnterFullscreen) {
      videoEl.webkitEnterFullscreen();
      return;
    }
    if (videoEl.requestFullscreen) {
      videoEl.requestFullscreen().catch(function () {});
      return;
    }
    if (videoEl.webkitRequestFullscreen) {
      videoEl.webkitRequestFullscreen();
    }
  }

  fsBtn.addEventListener("click", function (event) {
    event.stopPropagation();
    enterFullscreen();
  });

  videoEl.addEventListener("dblclick", enterFullscreen);

  function attachStream(hlsUrl) {
    if (window.Hls && Hls.isSupported()) {
      const hls = new Hls({
        lowLatencyMode: false,
        liveSyncDurationCount: 4,
        liveMaxLatencyDurationCount: 12,
        maxBufferLength: 40,
        backBufferLength: 60,
        maxLiveSyncPlaybackRate: 1,
        maxBufferHole: 0.5,
      });
      hls.loadSource(hlsUrl);
      hls.attachMedia(videoEl);
      hls.on(Hls.Events.MANIFEST_PARSED, function () {
        onPlaying();
        videoEl.muted = false;
        videoEl.play().catch(function () {
          videoEl.muted = true;
          videoEl.play().catch(function () {
            setStatus("Нажмите play на видео", "");
          });
        });
      });
      hls.on(Hls.Events.ERROR, function (_event, data) {
        if (!data.fatal) {
          return;
        }
        var detail = data.type || "unknown";
        if (data.response && data.response.code) {
          detail += ", HTTP " + data.response.code;
        }
        showOffline("Не удалось загрузить поток (" + detail + ")");
      });
      return;
    }

    if (videoEl.canPlayType("application/vnd.apple.mpegurl")) {
      videoEl.src = hlsUrl;
      videoEl.addEventListener("loadedmetadata", function () {
        onPlaying();
        videoEl.play().catch(function () {
          setStatus("Нажмите play на видео", "");
        });
      });
      videoEl.addEventListener("error", function () {
        showOffline("Не удалось загрузить поток");
      });
      return;
    }

    showOffline("Плеер не поддерживает HLS");
  }

  fetch("/api/live/config")
    .then(function (resp) {
      return resp.json();
    })
    .then(function (cfg) {
      if (!cfg.enabled) {
        titleEl.textContent = "Трансляция";
        showOffline("Трансляция не настроена");
        return;
      }
      titleEl.textContent = cfg.title || "Трансляция";
      return fetch("/api/live/status")
        .then(function (resp) {
          return resp.json();
        })
        .then(function (status) {
          if (!status.live) {
            showOffline("Сейчас эфира нет");
            return;
          }
          setStatus("Подключаемся…", "");
          attachStream(cfg.hlsUrl);
        });
    })
    .catch(function () {
      showOffline("Не удалось загрузить настройки");
    });
})();
