class NanoleafPanelPainterCard extends HTMLElement {
  setConfig(config) {
    if (!config.layout_entity) {
      throw new Error("layout_entity is required");
    }

    this._config = {
      brightness: 200,
      default_color: "#ff66cc",
      framebuffer_entity: undefined,
      brush_entity: undefined,
      show_labels: true,
      ...config,
    };

    this._color = this._config.default_color;
    this._brightness = this._config.brightness;
    this._mode = "paint";
    this._isPainting = false;
    this._paintedThisDrag = new Set();
    this._optimisticFills = new Map();
    this._layoutSignature = null;
    this._rendered = false;

    if (!this.shadowRoot) {
      this.attachShadow({ mode: "open" });
    }
  }

  set hass(hass) {
    this._hass = hass;

    const layout = this._getLayout();
    const signature = this._layoutSignatureFor(layout);

    if (!this._rendered || signature !== this._layoutSignature) {
      this._layoutSignature = signature;
      this._render(layout);
      this._rendered = true;
    }

    this._syncFromHass();
  }

  getCardSize() {
    return 6;
  }

  _getLayout() {
    const entityId = this._config.layout_entity;
    const state = this._hass?.states?.[entityId];
    return state || null;
  }

  _layoutSignatureFor(layoutState) {
    if (!layoutState) return "missing";
    const panels = layoutState.attributes?.panels || [];
    return JSON.stringify(
      panels.map((panel) => [
        panel.panel_id,
        panel.x,
        panel.y,
        panel.shape_type,
        panel.orientation,
        panel.polygon,
        panel.mqtt_command_topic,
      ]),
    );
  }

  _render(layoutState) {
    if (!this.shadowRoot || !this._hass || !this._config) return;

    if (!layoutState) {
      this.shadowRoot.innerHTML = `
        <ha-card>
          <div class="warning">
            Layout entity not found: ${this._config.layout_entity}
          </div>
        </ha-card>
      `;
      return;
    }

    const panels = layoutState.attributes.panels || [];
    const bounds =
      layoutState.attributes.bounds || this._calculateBounds(panels);

    const pad = 24;
    const minX = Number(bounds.min_x ?? 0) - pad;
    const minY = Number(bounds.min_y ?? 0) - pad;
    const width = Math.max(1, Number(bounds.width ?? 300) + pad * 2);
    const height = Math.max(1, Number(bounds.height ?? 300) + pad * 2);

    const usesBrushEntity = Boolean(this._config.brush_entity);

    this.shadowRoot.innerHTML = `
      <style>
        ha-card {
          padding: 16px;
          overflow: hidden;
        }

        .toolbar {
          display: grid;
          grid-template-columns: 1fr auto;
          gap: 12px;
          align-items: center;
          margin-bottom: 12px;
        }

        .local-controls {
          display: grid;
          grid-template-columns: auto 1fr auto;
          gap: 12px;
          align-items: center;
        }

        .brush-controls {
          display: flex;
          gap: 8px;
          align-items: center;
          min-width: 0;
        }

        .brush-swatch {
          width: 32px;
          height: 32px;
          border-radius: 999px;
          border: 2px solid var(--divider-color);
          flex: 0 0 auto;
          background: ${this._color};
        }

        .brush-label {
          overflow: hidden;
          text-overflow: ellipsis;
          white-space: nowrap;
          opacity: 0.85;
        }

        input[type="color"] {
          width: 48px;
          height: 36px;
          border: none;
          background: none;
          padding: 0;
        }

        input[type="range"] {
          width: 100%;
        }

        .brightness {
          font-size: 0.9rem;
          opacity: 0.8;
          min-width: 42px;
          text-align: right;
        }

        .mode-buttons {
          display: flex;
          gap: 8px;
          justify-content: flex-end;
        }

        button {
          border: 1px solid var(--divider-color);
          border-radius: 999px;
          padding: 7px 12px;
          background: var(--card-background-color);
          color: var(--primary-text-color);
          cursor: pointer;
          font: inherit;
        }

        button.active {
          background: var(--primary-color);
          color: var(--text-primary-color);
          border-color: var(--primary-color);
        }

        button.secondary {
          border-radius: 8px;
        }

        svg {
          width: 100%;
          height: auto;
          touch-action: none;
          user-select: none;
          background: var(--card-background-color);
          border-radius: 12px;
        }

        polygon {
          cursor: pointer;
          transition: opacity 80ms linear, stroke-width 80ms linear, fill 120ms linear;
        }

        polygon:hover {
          opacity: 0.85;
          stroke-width: 5;
        }

        text {
          pointer-events: none;
          font-family: var(--primary-font-family, sans-serif);
          font-weight: 700;
        }

        .warning {
          padding: 16px;
          color: var(--error-color);
        }

        .footer {
          margin-top: 8px;
          font-size: 0.85rem;
          opacity: 0.7;
        }
      </style>

      <ha-card>
        <div class="toolbar">
          ${usesBrushEntity ? this._renderBrushEntityControls() : this._renderLocalControls()}
          <div class="mode-buttons">
            <button id="paint-mode" class="active" type="button">Paint</button>
            <button id="erase-mode" type="button">Eraser</button>
          </div>
        </div>

        <svg
          id="panel-map"
          viewBox="${minX} ${minY} ${width} ${height}"
          role="img"
          aria-label="Nanoleaf panel painter"
        >
          ${panels.map((panel) => this._renderPanel(panel)).join("")}
        </svg>

        <div class="footer">
          ${
            usesBrushEntity
              ? "Use the brush light's native Home Assistant colour picker, then click or drag across panels."
              : "Click or drag across panels to paint. Use Eraser to turn panels off."
          }
        </div>
      </ha-card>
    `;

    this._wireEvents(panels);
    this._syncModeButtons();
  }

  _renderLocalControls() {
    return `
      <div class="local-controls">
        <input id="color" type="color" value="${this._color}">
        <input id="brightness" type="range" min="1" max="255" value="${this._brightness}">
        <div class="brightness">${this._brightness}</div>
      </div>
    `;
  }

  _renderBrushEntityControls() {
    return `
      <div class="brush-controls">
        <div class="brush-swatch" id="brush-swatch"></div>
        <div class="brush-label" id="brush-label">Brush</div>
        <button id="open-brush" class="secondary" type="button">Open picker</button>
      </div>
    `;
  }

  _renderPanel(panel) {
    const points = (panel.polygon || [])
      .map((point) => `${point.x},${point.y}`)
      .join(" ");

    const center = this._panelCenter(panel);
    const panelId = String(panel.panel_id);
    const fill = this._fillForPanel(panelId);
    const textFill = this._textColorForFill(fill);

    return `
      <g data-panel-id="${panelId}">
        <polygon
          data-panel-id="${panelId}"
          points="${points}"
          fill="${fill}"
          stroke="var(--divider-color)"
          stroke-width="3"
          stroke-linejoin="round"
        >
          <title>Panel ${panelId}</title>
        </polygon>

        ${
          this._config.show_labels
            ? `
          <text
            data-panel-label="${panelId}"
            x="${center.x}"
            y="${center.y}"
            text-anchor="middle"
            dominant-baseline="central"
            font-size="14"
            fill="${textFill}"
          >${panelId}</text>`
            : ""
        }
      </g>
    `;
  }

  _wireEvents(panels) {
    const colorInput = this.shadowRoot.getElementById("color");
    const brightnessInput = this.shadowRoot.getElementById("brightness");
    const svg = this.shadowRoot.getElementById("panel-map");
    const paintMode = this.shadowRoot.getElementById("paint-mode");
    const eraseMode = this.shadowRoot.getElementById("erase-mode");
    const openBrush = this.shadowRoot.getElementById("open-brush");

    if (colorInput) {
      colorInput.addEventListener("input", (event) => {
        this._color = event.target.value;
      });
    }

    if (brightnessInput) {
      brightnessInput.addEventListener("input", (event) => {
        this._brightness = Number(event.target.value);
        const label = this.shadowRoot.querySelector(".brightness");
        if (label) label.textContent = String(this._brightness);
      });
    }

    paintMode?.addEventListener("click", () => {
      this._mode = "paint";
      this._syncModeButtons();
    });

    eraseMode?.addEventListener("click", () => {
      this._mode = "erase";
      this._syncModeButtons();
    });

    openBrush?.addEventListener("click", () => {
      this._openMoreInfo(this._config.brush_entity);
    });

    const panelsById = new Map(
      panels.map((panel) => [String(panel.panel_id), panel]),
    );

    svg.addEventListener("pointerdown", (event) => {
      this._isPainting = true;
      this._paintedThisDrag.clear();
      svg.setPointerCapture(event.pointerId);
      this._paintFromEvent(event, panelsById);
    });

    svg.addEventListener("pointermove", (event) => {
      if (!this._isPainting) return;
      this._paintFromEvent(event, panelsById);
    });

    svg.addEventListener("pointerup", (event) => {
      this._isPainting = false;
      this._paintedThisDrag.clear();
      try {
        svg.releasePointerCapture(event.pointerId);
      } catch (_) {}
    });

    svg.addEventListener("pointercancel", () => {
      this._isPainting = false;
      this._paintedThisDrag.clear();
    });
  }

  _syncFromHass() {
    this._cleanupOptimisticFills();
    this._syncBrushDisplay();
    this._syncPanelFills();
  }

  _syncBrushDisplay() {
    if (!this._config.brush_entity) return;

    const brush = this._currentBrush();
    const swatch = this.shadowRoot.getElementById("brush-swatch");
    const label = this.shadowRoot.getElementById("brush-label");

    if (swatch) swatch.style.background = brush.hex;
    if (label)
      label.textContent = `${this._friendlyName(this._config.brush_entity)} · ${brush.brightness}`;
  }

  _syncPanelFills() {
    const layout = this._getLayout();
    const panels = layout?.attributes?.panels || [];

    for (const panel of panels) {
      const panelId = String(panel.panel_id);
      const fill = this._fillForPanel(panelId);
      const polygon = this.shadowRoot.querySelector(
        `polygon[data-panel-id="${panelId}"]`,
      );
      const label = this.shadowRoot.querySelector(
        `text[data-panel-label="${panelId}"]`,
      );

      if (polygon) polygon.setAttribute("fill", fill);
      if (label) label.setAttribute("fill", this._textColorForFill(fill));
    }
  }

  _syncModeButtons() {
    const paintMode = this.shadowRoot.getElementById("paint-mode");
    const eraseMode = this.shadowRoot.getElementById("erase-mode");
    paintMode?.classList.toggle("active", this._mode === "paint");
    eraseMode?.classList.toggle("active", this._mode === "erase");
  }

  _paintFromEvent(event, panelsById) {
    const element = this.shadowRoot.elementFromPoint(
      event.clientX,
      event.clientY,
    );

    const panelElement = element?.closest?.("[data-panel-id]");
    if (!panelElement) return;

    const panelId = panelElement.dataset.panelId;
    if (!panelId || this._paintedThisDrag.has(panelId)) return;

    const panel = panelsById.get(panelId);
    if (!panel) return;

    this._paintedThisDrag.add(panelId);

    if (this._mode === "erase") {
      this._erasePanel(panel);
    } else {
      this._paintPanel(panel);
    }
  }

  _paintPanel(panel) {
    const brush = this._currentBrush();

    const payload = {
      state: "ON",
      brightness: brush.brightness,
      color: brush.rgb,
    };

    this._publishPanelCommand(panel, payload);
    this._setOptimisticFill(String(panel.panel_id), brush.renderedHex);
  }

  _erasePanel(panel) {
    this._publishPanelCommand(panel, { state: "OFF" });
    this._setOptimisticFill(String(panel.panel_id), "#222831");
  }

  _publishPanelCommand(panel, payload) {
    if (!panel.mqtt_command_topic) {
      console.warn("Panel is missing mqtt_command_topic", panel);
      return;
    }

    this._hass.callService("mqtt", "publish", {
      topic: panel.mqtt_command_topic,
      payload: JSON.stringify(payload),
      qos: 0,
      retain: false,
    });
  }

  _currentBrush() {
    if (this._config.brush_entity) {
      const state = this._hass.states[this._config.brush_entity];
      if (state) {
        const rgb = this._rgbFromEntity(state) || this._hexToRgb(this._color);
        const brightness = Number(
          state.attributes?.brightness ?? this._brightness ?? 200,
        );
        return this._brushFromRgb(rgb, brightness);
      }
    }

    return this._brushFromRgb(this._hexToRgb(this._color), this._brightness);
  }

  _brushFromRgb(rgb, brightness) {
    const clampedBrightness = Math.max(
      1,
      Math.min(255, Math.round(Number(brightness || 200))),
    );
    const cleanRgb = {
      r: this._clamp(rgb.r),
      g: this._clamp(rgb.g),
      b: this._clamp(rgb.b),
    };
    const renderedRgb = {
      r: this._clamp(cleanRgb.r * (clampedBrightness / 255)),
      g: this._clamp(cleanRgb.g * (clampedBrightness / 255)),
      b: this._clamp(cleanRgb.b * (clampedBrightness / 255)),
    };

    return {
      rgb: cleanRgb,
      brightness: clampedBrightness,
      hex: this._rgbToHex(cleanRgb),
      renderedHex: this._rgbToHex(renderedRgb),
    };
  }

  _rgbFromEntity(state) {
    const attrs = state.attributes || {};

    if (Array.isArray(attrs.rgb_color) && attrs.rgb_color.length >= 3) {
      return {
        r: Number(attrs.rgb_color[0]),
        g: Number(attrs.rgb_color[1]),
        b: Number(attrs.rgb_color[2]),
      };
    }

    if (Array.isArray(attrs.hs_color) && attrs.hs_color.length >= 2) {
      return this._hsToRgb(
        Number(attrs.hs_color[0]),
        Number(attrs.hs_color[1]),
      );
    }

    return null;
  }

  _fillForPanel(panelId) {
    const optimistic = this._optimisticFills.get(String(panelId));
    if (optimistic && optimistic.expiresAt > Date.now()) {
      return optimistic.fill;
    }

    const framebuffer = this._getFramebufferPanel(panelId);
    if (!framebuffer || framebuffer.state !== "ON") {
      return "#222831";
    }

    if (framebuffer.rendered_color) {
      return this._rgbToHex(framebuffer.rendered_color);
    }

    if (framebuffer.color) {
      const brightness = Number(framebuffer.brightness ?? 255) / 255;
      return this._rgbToHex({
        r: Number(framebuffer.color.r ?? 0) * brightness,
        g: Number(framebuffer.color.g ?? 0) * brightness,
        b: Number(framebuffer.color.b ?? 0) * brightness,
      });
    }

    return "#222831";
  }

  _getFramebufferPanel(panelId) {
    const entityId = this._config.framebuffer_entity;
    if (!entityId) return null;

    const state = this._hass?.states?.[entityId];
    const panels = state?.attributes?.panels;
    if (!panels) return null;

    return panels[String(panelId)] || panels[Number(panelId)] || null;
  }

  _setOptimisticFill(panelId, fill) {
    this._optimisticFills.set(String(panelId), {
      fill,
      expiresAt: Date.now() + 1500,
    });

    const polygon = this.shadowRoot.querySelector(
      `polygon[data-panel-id="${panelId}"]`,
    );
    const label = this.shadowRoot.querySelector(
      `text[data-panel-label="${panelId}"]`,
    );

    if (polygon) polygon.setAttribute("fill", fill);
    if (label) label.setAttribute("fill", this._textColorForFill(fill));
  }

  _cleanupOptimisticFills() {
    const now = Date.now();
    for (const [panelId, value] of this._optimisticFills.entries()) {
      if (value.expiresAt <= now) {
        this._optimisticFills.delete(panelId);
      }
    }
  }

  _openMoreInfo(entityId) {
    if (!entityId) return;
    const event = new Event("hass-more-info", {
      bubbles: true,
      composed: true,
    });
    event.detail = { entityId };
    this.dispatchEvent(event);
  }

  _friendlyName(entityId) {
    const state = this._hass?.states?.[entityId];
    return state?.attributes?.friendly_name || entityId;
  }

  _hexToRgb(hex) {
    const value = String(hex || "#ff66cc").replace("#", "");
    return {
      r: parseInt(value.slice(0, 2), 16),
      g: parseInt(value.slice(2, 4), 16),
      b: parseInt(value.slice(4, 6), 16),
    };
  }

  _rgbToHex(rgb) {
    return `#${[rgb.r, rgb.g, rgb.b]
      .map((value) => this._clamp(value).toString(16).padStart(2, "0"))
      .join("")}`;
  }

  _hsToRgb(h, s) {
    s = Math.max(0, Math.min(100, s)) / 100;
    h = ((h % 360) + 360) % 360;

    const c = s;
    const x = c * (1 - Math.abs(((h / 60) % 2) - 1));
    const m = 1 - c;

    let r = 0;
    let g = 0;
    let b = 0;

    if (h < 60) [r, g, b] = [c, x, 0];
    else if (h < 120) [r, g, b] = [x, c, 0];
    else if (h < 180) [r, g, b] = [0, c, x];
    else if (h < 240) [r, g, b] = [0, x, c];
    else if (h < 300) [r, g, b] = [x, 0, c];
    else [r, g, b] = [c, 0, x];

    return {
      r: (r + m) * 255,
      g: (g + m) * 255,
      b: (b + m) * 255,
    };
  }

  _clamp(value) {
    return Math.max(0, Math.min(255, Math.round(Number(value) || 0)));
  }

  _textColorForFill(fill) {
    const rgb = this._hexToRgb(fill);
    const luminance = 0.299 * rgb.r + 0.587 * rgb.g + 0.114 * rgb.b;
    return luminance > 150 ? "#101418" : "#ffffff";
  }

  _panelCenter(panel) {
    if (!panel.polygon || panel.polygon.length === 0) {
      return { x: panel.x || 0, y: -(panel.y || 0) };
    }

    const x =
      panel.polygon.reduce((sum, point) => sum + Number(point.x), 0) /
      panel.polygon.length;
    const y =
      panel.polygon.reduce((sum, point) => sum + Number(point.y), 0) /
      panel.polygon.length;

    return { x, y };
  }

  _calculateBounds(panels) {
    const points = panels.flatMap((panel) => panel.polygon || []);

    if (points.length === 0) {
      return {
        min_x: 0,
        min_y: 0,
        width: 300,
        height: 300,
      };
    }

    const xs = points.map((point) => Number(point.x));
    const ys = points.map((point) => Number(point.y));

    const minX = Math.min(...xs);
    const maxX = Math.max(...xs);
    const minY = Math.min(...ys);
    const maxY = Math.max(...ys);

    return {
      min_x: minX,
      min_y: minY,
      width: maxX - minX,
      height: maxY - minY,
    };
  }
}

customElements.define("nanoleaf-panel-painter-card", NanoleafPanelPainterCard);

window.customCards = window.customCards || [];
window.customCards.push({
  type: "nanoleaf-panel-painter-card",
  name: "Nanoleaf Panel Painter",
  preview: false,
  description:
    "Paint colours onto Nanoleaf panels using the bridge layout JSON.",
});
