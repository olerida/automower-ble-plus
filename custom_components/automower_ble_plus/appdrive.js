const DOMAIN = "automower_ble_plus";
const INPUT_INTERVAL = 110;
const COPY = {
  en: {
    mower: "Mower", guide: "Hold and drag to drive. Release to stop.",
    joystick: "Joystick. Hold and drag or use the arrow keys.", stop: "Stop",
    loading: "Looking for mowers…", empty: "No appDrive mower is available.",
    ready: "Ready", starting: "Connecting…", driving: "Driving", stopping: "Stopping…",
    stopped: "Stopped", failed: "appDrive could not confirm the command.",
  },
  es: {
    mower: "Cortacésped", guide: "Mantén pulsado y arrastra para conducir. Suelta para detenerte.",
    joystick: "Joystick. Mantén pulsado y arrastra o usa las flechas del teclado.", stop: "Detener",
    loading: "Buscando cortacéspedes…", empty: "No hay ningún cortacésped con appDrive disponible.",
    ready: "Preparado", starting: "Conectando…", driving: "Conduciendo", stopping: "Deteniendo…",
    stopped: "Detenido", failed: "appDrive no pudo confirmar la orden.",
  },
};
const KEYS = {
  ArrowUp: [0.65, 0], ArrowDown: [-0.65, 0],
  ArrowLeft: [0, -0.6], ArrowRight: [0, 0.6],
};

class AutomowerAppDrive extends HTMLElement {
  constructor() {
    super();
    const root = this.attachShadow({ mode: "open" });
    root.innerHTML = `
      <style>
        :host { display: block; color: var(--primary-text-color, #212121);
          font-family: var(--ha-font-family-body, system-ui, sans-serif); }
        main { max-width: 46rem; margin: 0 auto; padding: clamp(1.25rem, 5vw, 3rem); }
        header { border-bottom: 1px solid var(--divider-color, #ddd); padding-bottom: 1rem; }
        h1 { font-size: clamp(1.7rem, 4vw, 2.25rem); margin: 0 0 1rem; font-weight: 650; }
        label { display: block; font-size: 0.9rem; margin-bottom: 0.4rem; }
        select, button { font: inherit; color: inherit; }
        select { min-height: 2.75rem; width: min(100%, 23rem); padding: 0.45rem 0.7rem;
          border: 1px solid var(--divider-color, #bbb); border-radius: 6px;
          background: var(--card-background-color, #fff); }
        section { display: grid; justify-items: center; gap: 1.1rem; padding-top: 1.8rem; }
        p { margin: 0; text-align: center; color: var(--secondary-text-color, #555); }
        #stick { position: relative; display: grid; place-items: center; width: min(75vw, 16rem);
          aspect-ratio: 1; border: 2px solid var(--divider-color, #bbb); border-radius: 50%;
          background: var(--card-background-color, #fff); touch-action: none; user-select: none; cursor: grab;
          transition: border-color 120ms ease; }
        #stick:hover:not(:disabled), #stick[aria-pressed="true"] { border-color: var(--primary-color, #007c88); }
        #stick[aria-pressed="true"] { cursor: grabbing; }
        .mark { position: absolute; inset: 7%; font-size: 1.25rem; pointer-events: none; }
        .up, .right, .down, .left { position: absolute; line-height: 1; }
        .up { top: 0; left: calc(50% - 0.4em); } .down { bottom: 0; left: calc(50% - 0.4em); }
        .right { right: 0; top: calc(50% - 0.5em); } .left { left: 0; top: calc(50% - 0.5em); }
        #thumb { width: 28%; aspect-ratio: 1; border-radius: 50%;
          background: var(--primary-color, #007c88); pointer-events: none;
          transform: translate(var(--x, 0px), var(--y, 0px)); transition: transform 130ms ease; }
        #stick[aria-pressed="true"] #thumb { transition: none; }
        #stop { min-width: 8rem; min-height: 2.75rem; border: 1px solid currentColor;
          border-radius: 6px; background: transparent; font-weight: 650; cursor: pointer; }
        button:disabled { opacity: 0.45; cursor: default !important; }
        button:focus-visible, select:focus-visible { outline: 3px solid var(--primary-color, #007c88); outline-offset: 4px; }
        #status { min-height: 1.3em; }
        @media (prefers-reduced-motion: reduce) { * { transition: none !important; } }
      </style>
      <main>
        <header><h1>Automower appDrive</h1><label for="mower"></label><select id="mower"></select></header>
        <section>
          <p id="guide"></p>
          <button id="stick" type="button" aria-pressed="false" disabled>
            <span class="mark" aria-hidden="true"><span class="up">↑</span><span class="right">→</span>
            <span class="down">↓</span><span class="left">←</span></span><span id="thumb" aria-hidden="true"></span>
          </button>
          <button id="stop" type="button" disabled></button>
          <p id="status" role="status" aria-live="polite"></p>
        </section>
      </main>`;
    this.select = root.querySelector("select");
    this.stick = root.querySelector("#stick");
    this.stop = root.querySelector("#stop");
    this.status = root.querySelector("#status");
    this._localize(COPY.en);
    this.onHide = () => { if (document.hidden) this._release(); };
    this.onBlur = () => this._release();
    this.select.addEventListener("change", () => this._release());
    this.stick.addEventListener("pointerdown", (event) => this._pointerDown(event));
    this.stick.addEventListener("pointermove", (event) => this._pointerMove(event));
    for (const kind of ["pointerup", "pointercancel", "lostpointercapture"]) {
      this.stick.addEventListener(kind, ({ pointerId }) => {
        if (this.held?.input === pointerId) this._release();
      });
    }
    this.stick.addEventListener("keydown", (event) => this._keyDown(event));
    this.stick.addEventListener("keyup", ({ key }) => { if (this.held?.input === key) this._release(); });
    this.stick.addEventListener("blur", () => this._release());
    this.stop.addEventListener("click", () => this._release());
  }

  set hass(hass) {
    if (this._hass?.connection && this._hass.connection !== hass.connection) {
      this._release();
      this.loadedConnection = null;
    }
    this._hass = hass;
    this._localize(hass.language?.startsWith("es") ? COPY.es : COPY.en);
    this._load();
  }

  connectedCallback() {
    document.addEventListener("visibilitychange", this.onHide);
    window.addEventListener("blur", this.onBlur);
    window.addEventListener("pagehide", this.onBlur);
    this._load();
  }

  disconnectedCallback() {
    this._release();
    this.loadedConnection = null;
    document.removeEventListener("visibilitychange", this.onHide);
    window.removeEventListener("blur", this.onBlur);
    window.removeEventListener("pagehide", this.onBlur);
  }

  _localize(copy) {
    if (this.copy === copy) return;
    this.copy = copy;
    this.shadowRoot.querySelector("label").textContent = copy.mower;
    this.shadowRoot.querySelector("#guide").textContent = copy.guide;
    this.stick.setAttribute("aria-label", copy.joystick);
    this.stop.textContent = copy.stop;
  }

  async _load() {
    const hass = this._hass;
    if (!this.isConnected || !hass || this.loadedConnection === hass.connection) return;
    this.loadedConnection = hass.connection;
    this.stick.disabled = true;
    this.status.textContent = this.copy.loading;
    try {
      const mowers = await hass.callWS({ type: `${DOMAIN}/appdrive_list` });
      if (!this.isConnected || this.loadedConnection !== hass.connection) return;
      const selected = this.select.value;
      this.select.replaceChildren(...mowers.map(({ name, entry_id }) => new Option(name, entry_id)));
      if (mowers.some(({ entry_id }) => entry_id === selected)) this.select.value = selected;
      this.stick.disabled = !mowers.length;
      this.status.textContent = mowers.length ? this.copy.ready : this.copy.empty;
    } catch (error) {
      this.status.textContent = error?.message || this.copy.failed;
    }
  }

  _pointerDown(event) {
    if (event.button !== 0 || this.held || this.releasing || this.stick.disabled) return;
    event.preventDefault();
    this.stick.focus();
    this.stick.setPointerCapture(event.pointerId);
    this._start(event.pointerId, this._pointerAxes(event));
  }

  _pointerMove(event) {
    if (this.held?.input === event.pointerId) this._move(this._pointerAxes(event));
  }

  _pointerAxes({ clientX, clientY }) {
    const rect = this.stick.getBoundingClientRect();
    const steering = (clientX - rect.left - rect.width / 2) / (rect.width * 0.38);
    const forward = (rect.top + rect.height / 2 - clientY) / (rect.height * 0.38);
    const length = Math.hypot(forward, steering);
    return length < 0.08 ? [0, 0] : [forward / Math.max(1, length), steering / Math.max(1, length)];
  }

  _keyDown(event) {
    if (event.key === "Escape") this._release();
    if (!(event.key in KEYS)) return;
    event.preventDefault();
    if (this.held || this.releasing || this.stick.disabled || event.repeat) return;
    this._start(event.key, KEYS[event.key]);
  }

  _move([forward, steering]) {
    this.held.forward = forward;
    this.held.steering = steering;
    const reach = this.stick.clientWidth * 0.28;
    this.stick.style.setProperty("--x", `${steering * reach}px`);
    this.stick.style.setProperty("--y", `${-forward * reach}px`);
  }

  async _start(input, axes) {
    const hold = this.held = { input, hass: this._hass, entry_id: this.select.value };
    this._move(axes);
    this.stick.setAttribute("aria-pressed", "true");
    this.stop.disabled = false;
    this.select.disabled = true;
    this.status.textContent = this.copy.starting;
    try {
      hold.subscription = await hold.hass.connection.subscribeMessage(({ token }) => {
        if (this.held !== hold || hold.token) return;
        hold.token = token;
        this._send(hold);
      }, { type: `${DOMAIN}/appdrive_start`, entry_id: hold.entry_id }, { resubscribe: false });
      if (this.held !== hold) await hold.subscription();
    } catch (error) {
      if (this.held === hold) this._release(error);
    }
  }

  async _send(hold) {
    if (this.held !== hold) return;
    try {
      await hold.hass.callWS({
        type: `${DOMAIN}/appdrive_input`, entry_id: hold.entry_id, token: hold.token,
        forward: hold.forward, steering: hold.steering,
      });
      if (this.held !== hold) return;
      this.status.textContent = hold.forward || hold.steering ? this.copy.driving : this.copy.ready;
      hold.timer = setTimeout(() => this._send(hold), INPUT_INTERVAL);
    } catch (error) {
      if (this.held === hold) this._release(error);
    }
  }

  async _release(error) {
    const hold = this.held;
    if (!hold) return;
    this.held = null;
    this.releasing = Boolean(hold.token);
    clearTimeout(hold.timer);
    this.stick.setAttribute("aria-pressed", "false");
    this.stick.style.removeProperty("--x");
    this.stick.style.removeProperty("--y");
    this.stop.disabled = true;
    this.select.disabled = false;
    this.status.textContent = this.releasing ? this.copy.stopping : this.copy.ready;
    try {
      if (hold.token) await hold.hass.callWS({
        type: `${DOMAIN}/appdrive_release`, entry_id: hold.entry_id, token: hold.token,
      });
    } catch (releaseError) {
      error = releaseError;
    } finally {
      if (hold.subscription) hold.subscription().catch(() => {});
      this.releasing = false;
      if (!this.held) this.status.textContent = error?.message || (error ? this.copy.failed : this.copy.stopped);
    }
  }
}

customElements.define("automower-ble-appdrive", AutomowerAppDrive);
