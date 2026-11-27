const DOMAIN = "automower_ble_plus";
const LINE_TYPES = new Set(["transport_path", "poi_path", "charging_station"]);
const COLORS = {
  work_area: "#287750",
  stay_out_zone: "#b14b52",
  rewilding_zone: "#b14b52",
  island: "#b14b52",
  transport_area: "#386583",
  transport_path: "#386583",
  poi_path: "#147d83",
  charging_station: "#997015",
  unknown: "#727b81",
};
const COPY = {
  en: {
    title: "Automower maps", mower: "Mower", map: "Map", site: "Installation",
    zones: "AIM zones", generated: "Generated lawn", reload: "Reload",
    features: "Map features", unselected: "Select a feature", empty: "No features in this map.",
    loading: "Reading the map over Bluetooth…", discovering: "Looking for mowers…",
    noMowers: "No mower with a compatible map is available.", saved: "Saved on the mower.",
    unavailable: "The mower has no map of this type yet.", failed: "The mower did not confirm the request.",
    add: "Add feature", type: "Feature type", name: "Name", width: "Width (mm)",
    enabled: "Enabled", geometry: "Edit outline", settings: "Edit settings", remove: "Delete",
    save: "Save", cancel: "Cancel", fit: "Fit map", zoomIn: "Zoom in", zoomOut: "Zoom out",
    point: "Point", addPoint: "Add point", removePoint: "Remove point",
    usePosition: "Use mower position", points: "Points (mm)", metre: "m",
    canvas: "Map of the mower installation", draw: "Click to add a point or drag a point to move it.",
    active: "Mower position", pick: "Select a feature on the map or in the list.",
    deleteTitle: "Delete this feature?", deleteArea: "Linked mowing missions will also be removed.",
    work_area: "Work area", stay_out_zone: "Stay-out zone", rewilding_zone: "Rewilding zone",
    island: "Island", transport_area: "Transport area", transport_path: "Transport path",
    poi_path: "Point-of-interest path", charging_station: "Charging station", unknown: "Map feature",
  },
  es: {
    title: "Mapas Automower", mower: "Cortacésped", map: "Mapa", site: "Instalación",
    zones: "Zonas AIM", generated: "Césped generado", reload: "Recargar",
    features: "Elementos del mapa", unselected: "Selecciona un elemento",
    empty: "Este mapa está vacío.", loading: "Leyendo el mapa por Bluetooth…",
    discovering: "Buscando cortacéspedes…", noMowers: "No hay cortacéspedes con mapas compatibles.",
    saved: "Guardado en el cortacésped.", unavailable: "El cortacésped todavía no tiene este mapa.",
    failed: "El cortacésped no ha confirmado la solicitud.", add: "Añadir elemento",
    type: "Tipo de elemento", name: "Nombre", width: "Anchura (mm)", enabled: "Activado",
    geometry: "Editar contorno", settings: "Editar ajustes", remove: "Eliminar",
    save: "Guardar", cancel: "Cancelar", fit: "Ajustar mapa", zoomIn: "Acercar", zoomOut: "Alejar",
    point: "Punto", addPoint: "Añadir punto", removePoint: "Quitar punto",
    usePosition: "Usar posición del robot", points: "Puntos (mm)", metre: "m",
    canvas: "Mapa de la instalación del cortacésped",
    draw: "Pulsa el plano para añadir un punto o arrastra un punto para moverlo.",
    active: "Posición del cortacésped", pick: "Selecciona un elemento en el plano o en la lista.",
    deleteTitle: "¿Eliminar este elemento?",
    deleteArea: "También se eliminarán las misiones de corte vinculadas.",
    work_area: "Área de trabajo", stay_out_zone: "Zona de exclusión",
    rewilding_zone: "Zona de renaturalización", island: "Isla", transport_area: "Área de paso",
    transport_path: "Ruta de paso", poi_path: "Ruta al punto de interés",
    charging_station: "Estación de carga", unknown: "Elemento del mapa",
  },
};

function labeledOption(value, label) {
  const option = document.createElement("option");
  option.value = value;
  option.textContent = label;
  return option;
}

function pointNearSegment(x, y, a, b) {
  const dx = b.x - a.x;
  const dy = b.y - a.y;
  const distance = dx * dx + dy * dy;
  const fraction = distance ? Math.max(0, Math.min(1, ((x - a.x) * dx + (y - a.y) * dy) / distance)) : 0;
  return Math.hypot(x - a.x - fraction * dx, y - a.y - fraction * dy) < 9;
}

function containsPoint(points, x, y, line) {
  let inside = false;
  for (let i = 0; i < points.length; i++) {
    const a = points[i];
    const b = points[(i + 1) % points.length];
    if ((i + 1 < points.length || !line) && pointNearSegment(x, y, a, b)) return true;
    if ((a.y > y) !== (b.y > y) && x < (b.x - a.x) * (y - a.y) / (b.y - a.y) + a.x) {
      inside = !inside;
    }
  }
  return !line && inside;
}

class AutomowerMaps extends HTMLElement {
  constructor() {
    super();
    this.copy = COPY.en;
    this.mowers = [];
    this.features = [];
    this.background = [];
    this.selected = null;
    this.draft = null;
    this.busy = false;
    this.zoom = 1;
    this.center = { x_mm: 0, y_mm: 0 };
    this.extent = { width: 30000, height: 22000 };
    const root = this.attachShadow({ mode: "open" });
    root.innerHTML = `
      <style>
        :host { display: block; color: var(--primary-text-color, #202a26);
          font-family: var(--ha-font-family-body, system-ui, sans-serif);
          --line: var(--divider-color, #dce3de); --muted: var(--secondary-text-color, #516359);
          --accent: var(--primary-color, #267355); --surface: var(--card-background-color, #fff); }
        * { box-sizing: border-box; }
        [hidden] { display: none !important; }
        main { max-width: 90rem; padding: clamp(1rem, 3vw, 2.5rem); margin: auto; }
        h1 { font-size: clamp(1.6rem, 3.5vw, 2.4rem); font-weight: 650; letter-spacing: -0.035em; margin: 0 0 0.75rem; }
        h2 { font-size: 1rem; margin: 0; font-weight: 650; }
        p { margin: 0; }
        header { padding-bottom: 1rem; }
        .toolbar, .actions { display: flex; flex-wrap: wrap; align-items: end; gap: 0.55rem; }
        label { display: grid; gap: 0.28rem; font-size: 0.86rem; color: var(--muted); }
        select, input, button { font: inherit; color: var(--primary-text-color, #202a26);
          background: var(--surface); border: 1px solid var(--line); border-radius: 7px; min-height: 2.55rem; }
        select, input { padding: 0.46rem 0.6rem; min-width: 0; }
        .toolbar select { min-width: min(12rem, 100%); }
        button { cursor: pointer; padding: 0.4rem 0.72rem; font-weight: 550; }
        button:hover:not(:disabled) { border-color: var(--accent); }
        button:disabled { opacity: 0.55; cursor: default; }
        button:focus-visible, select:focus-visible, input:focus-visible, canvas:focus-visible {
          outline: 2px solid var(--accent); outline-offset: 2px; }
        .primary { background: var(--accent); color: var(--text-primary-color, #fff); border-color: transparent; }
        .danger { color: var(--error-color, #96327a); }
        #status { min-height: 1.5rem; color: var(--muted); margin-top: 0.65rem; font-size: 0.88rem; overflow-wrap: anywhere; }
        #status[data-error="true"] { color: var(--error-color, #a23941); }
        .workspace { display: grid; grid-template-columns: minmax(0, 1fr) minmax(14rem, 17rem);
          border-top: 1px solid var(--line); border-bottom: 1px solid var(--line); }
        .map { position: relative; min-width: 0; overflow: hidden;
          background: color-mix(in srgb, var(--surface) 93%, #66917c); }
        canvas { display: block; width: 100%; height: clamp(22rem, 63vh, 48rem); }
        canvas[data-drawing="true"] { touch-action: none; cursor: crosshair; }
        .mapTools { position: absolute; right: 0.7rem; bottom: 0.7rem; display: flex; gap: 0.32rem; }
        .mapTools button { min-width: 2.5rem; }
        #scale { position: absolute; bottom: 0.85rem; left: 0.85rem; pointer-events: none;
          border-bottom: 2px solid var(--muted); color: var(--muted); font-size: 0.73rem;
          text-align: center; padding-bottom: 0.15rem; font-variant-numeric: tabular-nums; }
        aside { padding: 1rem 0 1rem 1rem; border-left: 1px solid var(--line); min-width: 0; }
        #features-list { display: grid; margin: 0.55rem 0 0.8rem; max-height: 14rem; overflow: auto; }
        #features-list button { border: 0; border-bottom: 1px solid var(--line); border-radius: 0;
          text-align: left; padding-left: 0.45rem; display: flex; align-items: center; gap: 0.5rem;
          min-width: 0; font-weight: 450; }
        #features-list button span { overflow-wrap: anywhere; }
        #features-list button i { width: 0.55rem; height: 0.55rem; border-radius: 50%; flex: none; }
        #features-list button[aria-pressed="true"] { background: color-mix(in srgb, var(--surface) 85%, var(--accent)); }
        #selected-title { overflow-wrap: anywhere; }
        #selected-actions, #draft { display: grid; gap: 0.65rem; }
        #add { margin-top: 0.65rem; }
        #editor-note, #selection-hint { color: var(--muted); font-size: 0.82rem; }
        #points { display: grid; gap: 0.28rem; max-height: 12rem; overflow: auto; }
        .point { display: grid; grid-template-columns: 1fr 1fr auto; gap: 0.24rem; align-items: center; }
        .point input { width: 100%; height: 2rem; padding: 0.25rem; font-variant-numeric: tabular-nums; }
        .point button { padding-inline: 0.45rem; }
        #enabled-field { display: flex; flex-direction: row; align-items: center; }
        #enabled-field input { min-height: auto; accent-color: var(--accent); }
        dialog { max-width: min(28rem, 90vw); border: 1px solid var(--line); border-radius: 8px;
          background: var(--surface); color: inherit; padding: 1.25rem; }
        dialog::backdrop { background: #14221c80; }
        dialog p { margin: 0.7rem 0 1rem; }
        @media (max-width: 700px) {
          .workspace { grid-template-columns: 1fr; }
          aside { padding: 0.8rem 0; border-left: 0; border-top: 1px solid var(--line); }
          canvas { height: clamp(18rem, 49svh, 29rem); }
          .toolbar select { min-width: 8rem; }
        }
      </style>
      <main>
        <header>
          <h1 data-text="title"></h1>
          <div class="toolbar" id="toolbar" hidden>
            <label><span data-text="mower"></span><select id="mowers"></select></label>
            <label><span data-text="map"></span><select id="maps"></select></label>
            <button type="button" id="reload" data-text="reload"></button>
          </div>
          <p id="status" role="status" aria-live="polite"></p>
        </header>
        <div class="workspace" id="workspace" hidden>
          <div class="map">
            <canvas id="canvas" tabindex="0"></canvas>
            <span id="scale"></span>
            <div class="mapTools">
              <button type="button" id="zoom-out">−</button>
              <button type="button" id="zoom-in">+</button>
              <button type="button" id="fit" data-text="fit"></button>
            </div>
          </div>
          <aside>
            <section id="feature-view">
              <h2 data-text="features"></h2>
              <div id="features-list"></div>
              <div id="selected-actions" hidden>
                <strong id="selected-title"></strong>
                <div class="actions">
                  <button type="button" id="geometry" data-text="geometry"></button>
                  <button type="button" id="settings" data-text="settings"></button>
                  <button type="button" id="remove" class="danger" data-text="remove"></button>
                </div>
              </div>
              <p id="selection-hint"></p>
              <button type="button" id="add" class="primary" data-text="add"></button>
            </section>
            <form id="draft" hidden>
              <h2 id="editor-title"></h2>
              <label id="type-field"><span data-text="type"></span><select id="type"></select></label>
              <label id="name-field"><span data-text="name"></span><input id="name" type="text"></label>
              <label id="width-field"><span data-text="width"></span><input id="width" type="number" step="1"></label>
              <label id="enabled-field"><input id="enabled" type="checkbox"><span data-text="enabled"></span></label>
              <div id="geometry-fields">
                <p id="editor-note" data-text="draw"></p>
                <div class="actions">
                  <button type="button" id="add-point" data-text="addPoint"></button>
                  <button type="button" id="position-point" data-text="usePosition"></button>
                </div>
                <p data-text="points"></p>
                <div id="points"></div>
              </div>
              <div class="actions">
                <button type="submit" id="save" class="primary" data-text="save"></button>
                <button type="button" id="cancel" data-text="cancel"></button>
              </div>
            </form>
          </aside>
        </div>
        <dialog id="delete-dialog" aria-labelledby="delete-title">
          <h2 id="delete-title" data-text="deleteTitle"></h2>
          <p id="delete-detail"></p>
          <form method="dialog" class="actions">
            <button value="cancel" data-text="cancel" autofocus></button>
            <button value="delete" class="danger" data-text="remove"></button>
          </form>
        </dialog>
      </main>`;
    this.ui = Object.fromEntries([...root.querySelectorAll("[id]")].map(el => [el.id, el]));
    this.canvas = this.ui.canvas;
    this.context = this.canvas.getContext("2d");
    this.observer = new ResizeObserver(() => this._draw());
    this._bind();
    this._localize();
  }

  _bind() {
    const ui = this.ui;
    ui.mowers.addEventListener("change", () => { this._chooseMower(); this._loadMap(); });
    ui.maps.addEventListener("change", () => this._loadMap());
    ui.reload.addEventListener("click", () => {
      if (this.mower) this._loadMap();
      else {
        this.loadedConnection = null;
        this._discover();
      }
    });
    ui.fit.addEventListener("click", () => { this._fit(); this._draw(); });
    for (const [id, factor] of [["zoom-in", 1.45], ["zoom-out", 1 / 1.45]]) {
      ui[id].addEventListener("click", () => {
        this.zoom = Math.max(0.15, Math.min(12, this.zoom * factor));
        this._draw();
      });
    }
    for (const mode of ["add", "geometry", "settings"]) {
      ui[mode].addEventListener("click", () => this._begin(mode));
    }
    ui.cancel.addEventListener("click", () => this._closeDraft());
    ui.draft.addEventListener("submit", event => { event.preventDefault(); this._save(); });
    ui.type.addEventListener("change", () => {
      this.draft.type = ui.type.value;
      this._toggleEditorFields();
    });
    ui.name.addEventListener("input", () => this._renderActions());
    ui.width.addEventListener("input", () => this._renderActions());
    ui["add-point"].addEventListener("click", () => this._addPoint({ ...this.center }));
    ui["position-point"].addEventListener("click", () => this._addMowerPoint());
    ui.remove.addEventListener("click", () => {
      const feature = this.selected;
      if (!feature?.editable) return;
      ui["delete-detail"].textContent = [feature.name || this.copy[feature.type],
        feature.type === "work_area" ? this.copy.deleteArea : ""].filter(Boolean).join(" ");
      this._status("");
      ui["delete-dialog"].returnValue = "cancel";
      ui["delete-dialog"].showModal();
    });
    ui["delete-dialog"].addEventListener("close", () => {
      if (ui["delete-dialog"].returnValue === "delete" && this.selected?.editable) {
        this._write("delete_map_feature", { feature_id: this.selected.id });
      }
    });
    this.canvas.addEventListener("pointerdown", event => this._pointerDown(event));
    this.canvas.addEventListener("pointermove", event => this._pointerMove(event));
    for (const kind of ["pointerup", "pointercancel", "lostpointercapture"]) {
      this.canvas.addEventListener(kind, event => {
        if (event.pointerId === this.drag?.id) {
          this.drag = null;
          this._renderPoints();
        }
      });
    }
  }

  set hass(hass) {
    const connectionChanged = this._hass?.connection !== hass.connection;
    const copy = hass.language?.startsWith("es") ? COPY.es : COPY.en;
    this._hass = hass;
    if (this.copy !== copy) {
      this.copy = copy;
      this._localize();
    }
    if (connectionChanged || !this.loadedConnection) this._discover();
  }

  connectedCallback() {
    this.observer.observe(this.canvas);
    this._discover();
  }

  disconnectedCallback() {
    this.observer.disconnect();
    this.loadedConnection = null;
    this.sequence = (this.sequence || 0) + 1;
  }

  _localize() {
    for (const el of this.shadowRoot.querySelectorAll("[data-text]")) {
      el.textContent = this.copy[el.dataset.text];
    }
    this.canvas.setAttribute("aria-label", this.copy.canvas);
    this.ui["zoom-in"].ariaLabel = this.copy.zoomIn;
    this.ui["zoom-out"].ariaLabel = this.copy.zoomOut;
    if (this.ui.maps.options.length) {
      for (const option of this.ui.maps.options) option.textContent = this.copy[option.value];
    }
    this._listFeatures();
    this._renderActions();
  }

  _status(text = "", error = false) {
    this.ui.status.textContent = text;
    this.ui.status.dataset.error = String(error);
  }

  async _discover() {
    const connection = this._hass?.connection;
    if (!this.isConnected || !connection || this.loadedConnection === connection) return;
    this.loadedConnection = connection;
    this.sequence = (this.sequence || 0) + 1;
    const sequence = this.sequence;
    this.busy = false;
    this._closeDraft();
    this.mower = null;
    this.ui.toolbar.hidden = true;
    this.ui.workspace.hidden = true;
    this._status(this.copy.discovering);
    try {
      const mowers = await this._hass.callWS({ type: `${DOMAIN}/maps_list` });
      if (!this.isConnected || sequence !== this.sequence) return;
      this.mowers = mowers;
      if (!mowers.length) {
        this._showDiscoveryRetry();
        this._status(this.copy.noMowers);
        return;
      }
      this.ui.mowers.replaceChildren(...mowers.map(mower => labeledOption(mower.entity_id, mower.name)));
      this.ui.mowers.parentElement.hidden = false;
      this.ui.maps.parentElement.hidden = false;
      this.ui.toolbar.hidden = false;
      this._chooseMower();
      await this._loadMap();
    } catch (error) {
      if (this.isConnected && sequence === this.sequence) {
        this._showDiscoveryRetry();
        this._status(error.message || this.copy.failed, true);
      }
    }
  }

  _showDiscoveryRetry() {
    this.ui.toolbar.hidden = false;
    this.ui.mowers.parentElement.hidden = true;
    this.ui.maps.parentElement.hidden = true;
  }

  _chooseMower() {
    this.mower = this.mowers.find(mower => mower.entity_id === this.ui.mowers.value);
    const maps = this.mower?.maps || [];
    this.ui.maps.replaceChildren(...maps.map(kind => labeledOption(kind, this.copy[kind])));
    this.ui.maps.value = maps.includes("site") ? "site" : maps.includes("zones") ? "zones" : maps[0] || "";
  }

  async _action(service, data = {}) {
    const entityId = this.mower.entity_id;
    const result = await this._hass.callWS({
      type: "call_service", domain: DOMAIN, service, target: { entity_id: entityId },
      service_data: data, return_response: true,
    });
    if (!result?.response?.[entityId]) throw new Error(this.copy.failed);
    return result.response[entityId];
  }

  async _loadMap(selectedId = null, note = "") {
    const kind = this.ui.maps.value;
    if (!this.mower || !kind || this.busy) return;
    const sequence = ++this.sequence;
    this._closeDraft();
    this.busy = true;
    this.map = null;
    this.selected = null;
    this.features = [];
    this.background = [];
    this.pose = null;
    this.ui.workspace.hidden = false;
    this._status(this.copy.loading);
    this._listFeatures();
    this._renderActions();
    this._draw();
    try {
      const map = await this._action("get_map_features", { map_type: kind });
      if (!this.isConnected || sequence !== this.sequence) return;
      this.map = map;
      this.features = map.features || [];
      this.selected = this.features.find(feature => feature.id === selectedId) || null;
      if (kind === "zones" && this.mower.maps.includes("generated")) {
        try {
          const background = await this._action("get_map_features", { map_type: "generated" });
          if (!this.isConnected || sequence !== this.sequence) return;
          this.background = background.features || [];
        } catch {
          // The editable zone data is already available.
        }
      }
      try {
        const position = (await this._action("get_position")).local;
        if (!this.isConnected || sequence !== this.sequence) return;
        if (Number.isInteger(position?.x_mm) && Number.isInteger(position?.y_mm)) this.pose = position;
      } catch {
        // Some firmware reports map geometry without a live local position.
      }
      if (!this.isConnected || sequence !== this.sequence) return;
      this._fit();
      this._status(note || (map.available || map.can_create ? "" : this.copy.unavailable));
    } catch (error) {
      if (this.isConnected && sequence === this.sequence) {
        this._status([note, error.message || this.copy.failed].filter(Boolean).join(" "), true);
      }
    } finally {
      if (this.isConnected && sequence === this.sequence) {
        this.busy = false;
        this._listFeatures();
        this._renderActions();
        this._draw();
      }
    }
  }

  _listFeatures() {
    const list = this.ui["features-list"];
    list.replaceChildren();
    for (const feature of this.features) {
      const row = document.createElement("button");
      const color = document.createElement("i");
      const label = document.createElement("span");
      row.type = "button";
      row.setAttribute("aria-pressed", String(this.selected === feature));
      color.style.backgroundColor = COLORS[feature.type] || COLORS.unknown;
      label.textContent = feature.name || this.copy[feature.type] || this.copy.unknown;
      row.append(color, label);
      row.addEventListener("click", () => this._select(feature));
      list.append(row);
    }
    this.ui["selection-hint"].textContent = this.features.length ? this.copy.pick : this.copy.empty;
    this.ui["selection-hint"].hidden = Boolean(this.selected) || !this.map;
  }

  _select(feature) {
    if (this.busy || this.draft) return;
    this.selected = feature;
    this._listFeatures();
    this._renderActions();
    this._draw();
  }

  _renderActions() {
    const ui = this.ui;
    for (const key of ["mowers", "maps", "reload"]) ui[key].disabled = this.busy || Boolean(this.draft);
    ui["feature-view"].hidden = Boolean(this.draft);
    ui["selected-actions"].hidden = !this.selected;
    ui["selected-title"].textContent = this.selected?.name || this.copy[this.selected?.type] || "";
    ui.add.hidden = !this.map?.can_create || !this.map?.creatable_types?.length;
    ui.add.disabled = this.busy;
    ui.geometry.hidden = !this.selected?.editable;
    ui.settings.hidden = !this.selected?.editable_settings?.length;
    ui.remove.hidden = !this.selected?.editable;
    for (const key of ["geometry", "settings", "remove"]) ui[key].disabled = this.busy;
    ui.draft.hidden = !this.draft;
    for (const control of ui.draft.elements) control.disabled = this.busy;
    if (!this.draft) return;
    for (const key of ["name", "width", "enabled", "type"]) {
      ui[key].disabled = this.busy || ui[`${key}-field`].hidden;
    }
    const editingGeometry = this.draft.mode !== "settings";
    this.canvas.dataset.drawing = String(editingGeometry && !this.busy);
    ui["position-point"].hidden = !this.pose;
    const validPoints = this.draft.points.every(point =>
      [point.x_mm, point.y_mm].every(value => Number.isInteger(value) && value >= -2147483648 && value <= 2147483647));
    ui.save.disabled = this.busy
      || (!ui["name-field"].hidden && !ui.name.value.trim())
      || (!ui["width-field"].hidden && (!ui.width.value || !ui.width.validity.valid))
      || (editingGeometry && (!validPoints || this.draft.points.length < (LINE_TYPES.has(this.draft.type) ? 2 : 3)));
  }

  _begin(mode) {
    if (this.busy || !this.map?.checksum) return;
    if (mode === "add" ? !this.map.can_create : !this.selected?.editable) return;
    const options = this.map.creatable_types || [];
    this.draft = {
      mode, type: mode === "add" ? options[0] : this.selected.type,
      points: mode === "add" ? [] : this.selected.points.map(point => ({ ...point })),
    };
    if (mode === "geometry" && !LINE_TYPES.has(this.draft.type)) {
      const first = this.draft.points[0];
      const last = this.draft.points.at(-1);
      if (first && last && first.x_mm === last.x_mm && first.y_mm === last.y_mm) {
        this.draft.points.pop();
      }
    }
    const ui = this.ui;
    ui["editor-title"].textContent = this.copy[mode];
    ui.type.replaceChildren(...options.map(type => labeledOption(type, this.copy[type])));
    ui.type.value = this.draft.type;
    ui.name.value = mode === "add" ? "" : this.selected.name;
    const limits = this.selected?.width_limits_mm || this.map.path_width_limits_mm || { min: 3000, max: 5000 };
    ui.width.min = limits.min;
    ui.width.max = limits.max;
    ui.width.value = mode === "add" ? limits.min : this.selected.width_mm ?? limits.min;
    ui.enabled.checked = mode === "add" || this.selected.enabled;
    this._status("");
    this._toggleEditorFields();
    this._renderPoints();
    if (mode === "add") ui.name.focus();
    else if (mode === "geometry") this.canvas.focus();
  }

  _toggleEditorFields() {
    const mode = this.draft.mode;
    const fields = this.selected?.editable_settings || [];
    this.ui["type-field"].hidden = mode !== "add";
    this.ui["name-field"].hidden = mode !== "add" && (mode !== "settings" || !fields.includes("name"));
    this.ui["width-field"].hidden = mode === "add"
      ? !LINE_TYPES.has(this.draft.type) : mode !== "settings" || !fields.includes("width_mm");
    this.ui["enabled-field"].hidden = mode !== "settings" || !fields.includes("enabled");
    this.ui["geometry-fields"].hidden = mode === "settings";
    this._renderActions();
    this._draw();
  }

  _closeDraft() {
    this.draft = null;
    this.drag = null;
    this.canvas.dataset.drawing = "false";
    this._renderActions();
    this._draw();
  }

  _addPoint(point) {
    if (this.busy || !this.draft || this.draft.mode === "settings") return;
    this.draft.points.push({ x_mm: point.x_mm, y_mm: point.y_mm });
    this._renderPoints();
    this._draw();
  }

  async _addMowerPoint() {
    if (this.busy || !this.draft || !this.pose) return;
    const draft = this.draft;
    this.busy = true;
    this._renderActions();
    try {
      const pose = (await this._action("get_position")).local;
      if (this.draft !== draft) return;
      if (!Number.isInteger(pose?.x_mm) || !Number.isInteger(pose?.y_mm)) throw new Error(this.copy.failed);
      this.pose = pose;
      draft.points.push({ x_mm: pose.x_mm, y_mm: pose.y_mm });
    } catch (error) {
      if (this.draft === draft) this._status(error.message || this.copy.failed, true);
    } finally {
      if (this.draft === draft) {
        this.busy = false;
        this._renderPoints();
        this._draw();
      }
    }
  }

  _renderPoints() {
    this.ui.points.replaceChildren();
    for (const [index, point] of (this.draft?.points || []).entries()) {
      const row = document.createElement("div");
      row.className = "point";
      for (const axis of ["x_mm", "y_mm"]) {
        const field = document.createElement("input");
        field.type = "number";
        field.step = "1";
        field.value = Number.isFinite(point[axis]) ? point[axis] : "";
        field.ariaLabel = `${this.copy.point} ${index + 1} ${axis}`;
        field.addEventListener("input", () => {
          point[axis] = field.valueAsNumber;
          this._renderActions();
          this._draw();
        });
        row.append(field);
      }
      const remove = document.createElement("button");
      remove.type = "button";
      remove.textContent = "×";
      remove.ariaLabel = `${this.copy.removePoint} ${index + 1}`;
      remove.addEventListener("click", () => {
        this.draft.points.splice(index, 1);
        this._renderPoints();
        this._draw();
      });
      row.append(remove);
      this.ui.points.append(row);
    }
    this._renderActions();
  }

  async _save() {
    if (!this.draft || this.ui.save.disabled || this.busy) return;
    const ui = this.ui;
    const draft = this.draft;
    if (draft.mode === "geometry") {
      await this._write("edit_map_geometry", { feature_id: this.selected.id, points: draft.points });
    } else if (draft.mode === "settings") {
      const fields = { feature_id: this.selected.id };
      if (!ui["name-field"].hidden) fields.name = ui.name.value.trim();
      if (!ui["enabled-field"].hidden) fields.enabled = ui.enabled.checked;
      if (!ui["width-field"].hidden) fields.width_mm = ui.width.valueAsNumber;
      await this._write("set_map_feature_settings", fields);
    } else {
      const fields = { feature_type: draft.type, name: ui.name.value.trim(), points: draft.points };
      if (!ui["width-field"].hidden) fields.width_mm = ui.width.valueAsNumber;
      await this._write("create_map_feature", fields);
    }
  }

  async _write(service, fields) {
    if (this.busy || !this.map?.checksum) return;
    const sequence = this.sequence;
    this.busy = true;
    this._status(this.copy.loading);
    this._renderActions();
    try {
      const revision = { map_type: this.map.map_type, expected_checksum: this.map.checksum };
      if (Number.isInteger(this.map.installation_id)) revision.expected_installation_id = this.map.installation_id;
      const result = await this._action(service, { ...fields, ...revision });
      if (!this.isConnected || sequence !== this.sequence) return;
      this.busy = false;
      await this._loadMap(result.feature_id, this.copy.saved);
    } catch (error) {
      if (this.isConnected && sequence === this.sequence) {
        this.busy = false;
        this._status(error.message || this.copy.failed, true);
        this._renderActions();
      }
    }
  }

  _fit() {
    let minX = Infinity, maxX = -Infinity, minY = Infinity, maxY = -Infinity;
    for (const shape of [...this.features, ...this.background]) {
      for (const point of shape.points) {
        minX = Math.min(minX, point.x_mm);
        maxX = Math.max(maxX, point.x_mm);
        minY = Math.min(minY, point.y_mm);
        maxY = Math.max(maxY, point.y_mm);
      }
    }
    this.zoom = 1;
    this.center = Number.isFinite(minX)
      ? { x_mm: (minX + maxX) / 2, y_mm: (minY + maxY) / 2 }
      : { x_mm: this.pose?.x_mm ?? 0, y_mm: this.pose?.y_mm ?? 0 };
    this.extent = Number.isFinite(minX)
      ? { width: Math.max(3000, maxX - minX), height: Math.max(3000, maxY - minY) }
      : { width: 30000, height: 22000 };
  }

  _toCanvas({ x_mm, y_mm }) {
    const { width, height, scale } = this.view;
    return { x: width / 2 + (x_mm - this.center.x_mm) * scale,
      y: height / 2 - (y_mm - this.center.y_mm) * scale };
  }

  _fromCanvas(x, y) {
    const { width, height, scale } = this.view;
    return { x_mm: Math.round(this.center.x_mm + (x - width / 2) / scale),
      y_mm: Math.round(this.center.y_mm - (y - height / 2) / scale) };
  }

  _pointerDown(event) {
    if (this.busy || !this.view || event.button !== 0) return;
    const rect = this.canvas.getBoundingClientRect();
    const x = event.clientX - rect.left, y = event.clientY - rect.top;
    if (this.draft?.mode === "settings") return;
    if (this.draft) {
      const index = this.draft.points.findIndex(point => {
        const node = this._toCanvas(point);
        return Math.hypot(node.x - x, node.y - y) <= 12;
      });
      if (index < 0) {
        this._addPoint(this._fromCanvas(x, y));
      } else {
        this.drag = { id: event.pointerId, index };
        this.canvas.setPointerCapture(event.pointerId);
      }
      return;
    }
    const feature = this.features.findLast(feature => containsPoint(
      feature.points.map(point => this._toCanvas(point)), x, y,
      LINE_TYPES.has(feature.type) || feature.geometry_type === "polyline"));
    this._select(feature || null);
  }

  _pointerMove(event) {
    if (!this.draft || this.drag?.id !== event.pointerId) return;
    const rect = this.canvas.getBoundingClientRect();
    this.draft.points[this.drag.index] = this._fromCanvas(
      event.clientX - rect.left, event.clientY - rect.top);
    this._draw();
  }

  _draw() {
    const { width, height } = this.canvas.getBoundingClientRect();
    if (!width || !height || !this.context) return;
    const pixelRatio = window.devicePixelRatio || 1;
    const pixelWidth = Math.round(width * pixelRatio);
    const pixelHeight = Math.round(height * pixelRatio);
    if (this.canvas.width !== pixelWidth) this.canvas.width = pixelWidth;
    if (this.canvas.height !== pixelHeight) this.canvas.height = pixelHeight;
    const ctx = this.context;
    ctx.setTransform(pixelRatio, 0, 0, pixelRatio, 0, 0);
    ctx.clearRect(0, 0, width, height);
    const scale = Math.min((width - 48) / this.extent.width, (height - 48) / this.extent.height) * this.zoom;
    if (scale <= 0) return;
    this.view = { width, height, scale };
    this._drawGrid();
    for (const feature of this.background) this._drawShape(feature, false, true);
    for (const feature of this.features) {
      if (feature !== this.selected) this._drawShape(feature);
    }
    if (this.selected) this._drawShape(this.selected, true, Boolean(this.draft?.mode === "geometry"));
    if (this.draft && this.draft.mode !== "settings") {
      const feature = { ...this.draft, enabled: true };
      this._drawShape(feature, true);
      ctx.fillStyle = "#fff";
      ctx.strokeStyle = COLORS[feature.type] || COLORS.work_area;
      ctx.lineWidth = 2;
      for (const point of feature.points.filter(point => Number.isInteger(point.x_mm) && Number.isInteger(point.y_mm))) {
        const { x, y } = this._toCanvas(point);
        ctx.beginPath();
        ctx.arc(x, y, 5, 0, Math.PI * 2);
        ctx.fill();
        ctx.stroke();
      }
    }
    if (this.pose) {
      const { x, y } = this._toCanvas(this.pose);
      ctx.fillStyle = "#116c84";
      ctx.strokeStyle = "#fff";
      ctx.lineWidth = 2;
      ctx.beginPath();
      ctx.arc(x, y, 5, 0, Math.PI * 2);
      ctx.fill();
      ctx.stroke();
      ctx.fillStyle = getComputedStyle(this).getPropertyValue("--muted").trim() || "#516359";
      ctx.font = "12px system-ui, sans-serif";
      ctx.fillText(this.copy.active, x + 10, y - 7);
    }
  }

  _drawGrid() {
    const ctx = this.context;
    const { width, height, scale } = this.view;
    const nominal = 70 / scale;
    const units = 10 ** Math.floor(Math.log10(nominal));
    const spacing = [1, 2, 5, 10].find(multiplier => multiplier * units >= nominal) * units;
    const step = spacing * scale;
    const origin = this._toCanvas({ x_mm: 0, y_mm: 0 });
    const mod = (offset, size) => ((offset % size) + size) % size;
    ctx.strokeStyle = getComputedStyle(this).getPropertyValue("--line").trim() || "#dce3de";
    ctx.lineWidth = 1;
    ctx.beginPath();
    for (let x = mod(origin.x, step); x < width; x += step) { ctx.moveTo(x, 0); ctx.lineTo(x, height); }
    for (let y = mod(origin.y, step); y < height; y += step) { ctx.moveTo(0, y); ctx.lineTo(width, y); }
    ctx.stroke();
    this.ui.scale.style.width = `${step}px`;
    this.ui.scale.textContent = `${spacing / 1000} ${this.copy.metre}`;
  }

  _drawShape(feature, highlighted = false, background = false) {
    const points = feature.points.filter(point => Number.isInteger(point.x_mm) && Number.isInteger(point.y_mm));
    if (points.length < 2) return;
    const ctx = this.context;
    const line = LINE_TYPES.has(feature.type) || feature.geometry_type === "polyline";
    const color = COLORS[feature.type] || COLORS.unknown;
    ctx.save();
    ctx.strokeStyle = color;
    ctx.lineWidth = highlighted ? 3 : 1.5;
    if (!feature.enabled) ctx.setLineDash([5, 5]);
    ctx.beginPath();
    for (const [index, point] of points.entries()) {
      const { x, y } = this._toCanvas(point);
      if (index === 0) ctx.moveTo(x, y);
      else ctx.lineTo(x, y);
    }
    if (!line && points.length >= 3) {
      ctx.closePath();
      ctx.fillStyle = color;
      ctx.globalAlpha = background ? 0.07 : highlighted ? 0.22 : 0.13;
      ctx.fill();
    }
    ctx.globalAlpha = background ? 0.4 : feature.enabled ? 1 : 0.55;
    ctx.stroke();
    ctx.restore();
  }
}

if (!customElements.get("automower-ble-maps")) customElements.define("automower-ble-maps", AutomowerMaps);
