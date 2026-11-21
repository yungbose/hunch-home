/*
  Silk: six laminar ribbons of blue light, after an earlier design of the author's, as on Hunch's Pi screen.
  Each ribbon is three strands whose centre line is a sum of three sines on incommensurate speeds; the blurred
  stroke is computed directly as a gaussian across each strand, in one fragment-shader pass over a small buffer
  that the compositor scales up. No blur pass, no readback, a handful of uniforms per frame.

  The ribbons are laid out in a 1280 x 720 space, as on that screen. Here the stage can be any shape: its height is
  720 units and its width follows its aspect, centred on the middle of that space, so a wide stage shows the
  ribbons fading out at both ends and a narrow one shows their middle.

  Phases and widths depend only on time, so the processor works them out once per frame (wrapped to one turn)
  and the shader never sees a large number: half-precision GPUs keep their accuracy.

  States ease towards targets: snap on, settle off.

  Renderings ("quality"):
    full   a quarter-size buffer at up to 60 frames a second
    light  a sixth-size buffer at up to 30 frames a second, for small or slow screens
    still  time stands still; frames are drawn only while a change of state settles, then drawing stops
  Whichever is chosen, frames that keep arriving late step the buffer down, then the frame rate.
  `pause(true)` stops all drawing (the page is hidden or the stage is scrolled away).
*/

const H = 720, MID = 640;

function hex(h) {
  return [parseInt(h.slice(1, 3), 16) / 255, parseInt(h.slice(3, 5), 16) / 255, parseInt(h.slice(5, 7), 16) / 255];
}
const PLATE = hex("#050505");
const SIGNAL = hex("#0027ff");
// ribbon light on black: signal and signal rule; the faint outer thread stays signal (a paler one reads as grey haze)
const ON_PLATE = [hex("#0027ff"), hex("#4d66ff"), hex("#0027ff")];
// ribbon light on the signal flood: paper-ward, so the silk reads as lighter streaks in the blue
const ON_FLOOD = [[0.36, 0.42, 0.62], [0.52, 0.56, 0.70], [0.80, 0.80, 0.82]];

// The page's states. Centres sit lower than on that screen, so the words at the top of the stage stay on the plate.
const TARGETS = {
  resting:  { half: 620, amp: 0.20, lift: 0.95, centre: 500, swirl: 0, flood: 0, beat: 6.0, voice: 0, grey: 0 },
  hearing:  { half: 820, amp: 0.66, lift: 1.2,  centre: 488, swirl: 0, flood: 0, beat: 0,   voice: 1, grey: 0 },
  // between speech and an answer, or while a tried sentence is weighed: the ribbons gather and brighten
  deciding: { half: 700, amp: 0.42, lift: 1.25, centre: 470, swirl: 0, flood: 0, beat: 1.1, voice: 0, grey: 0 },
  // Hunch did it: the signal floods the stage
  done:     { half: 980, amp: 0.24, lift: 0.36, centre: 456, swirl: 0, flood: 1, beat: 0,   voice: 0, grey: 0 },
  // passed on: the eddy turns the light away, to someone else
  passed:   { half: 600, amp: 0.30, lift: 0.85, centre: 470, swirl: 1, flood: 0, beat: 0,   voice: 0, grey: 0 },
  // Hunch did nothing (a command it would only be guessing at): the ribbon holds still where it is
  nothing:  { half: 640, amp: 0.12, lift: 0.80, centre: 500, swirl: 0, flood: 0, beat: 0,   voice: 0, grey: 0 },
  // ignored: the ribbon settles low and quiet
  ignored:  { half: 760, amp: 0.16, lift: 0.70, centre: 600, swirl: 0, flood: 0, beat: 0,   voice: 0, grey: 0 },
  // could not reach the other agent: the colour drains from the light, never red
  error:    { half: 640, amp: 0.14, lift: 0.60, centre: 520, swirl: 0, flood: 0, beat: 0,   voice: 0, grey: 1 }
};
const SWIRL_C = [930, 452];

const QUALITY = {
  full:  [{ scale: 0.25, fps: 60 }, { scale: 0.2, fps: 60 }, { scale: 0.2, fps: 30 }, { scale: 0.125, fps: 30 }],
  light: [{ scale: 0.16, fps: 30 }, { scale: 0.125, fps: 30 }, { scale: 0.1, fps: 20 }],
  still: [{ scale: 0.25, fps: 15 }]
};
const MAX_BUFFER = 400;   // the widest buffer, whatever the stage

const VERT = "attribute vec2 a;void main(){gl_Position=vec4(a,0.0,1.0);}";
const FRAG = [
  "#ifdef GL_FRAGMENT_PRECISION_HIGH",
  "precision highp float;",
  "#else",
  "precision mediump float;",
  "#endif",
  "uniform vec2 uRes;",
  "uniform float uView;",
  "uniform float uOff;",
  "uniform float uA[6];",
  "uniform float uB[6];",
  "uniform float uC[6];",
  "uniform float uW[18];",
  "uniform float uWave;",
  "uniform float uWaveFar;",
  "uniform float uCentre;",
  "uniform float uPitch;",
  "uniform float uCx;",
  "uniform float uHalf;",
  "uniform float uLift;",
  "uniform vec3 uBg;",
  "uniform vec3 uCol0;",
  "uniform vec3 uCol1;",
  "uniform vec3 uCol2;",
  "uniform vec2 uSwirlC;",
  "uniform float uTwist;",
  "uniform float uSwirl;",
  "void main(){",
  "  vec2 p=vec2(gl_FragCoord.x/uRes.x*uView+uOff,(1.0-gl_FragCoord.y/uRes.y)*720.0);",
  // the eddy: rotate the sampling point about a centre, more strongly near it, and draw the light inward
  "  vec2 d=p-uSwirlC;",
  "  float fall=exp(-dot(d,d)/135200.0);",
  "  float an=uTwist*fall;",
  "  float cs=cos(an);float sn=sin(an);",
  "  d=vec2(cs*d.x-sn*d.y,sn*d.x+cs*d.y)*(1.0+0.42*uSwirl*fall);",
  "  p=uSwirlC+d;",
  "  float ex=(p.x-uCx)/uHalf;",
  "  float env=exp(-ex*ex);",
  "  float k1=p.x*0.0060;float k2=p.x*0.0026;float k3=p.x*0.0115;",
  "  vec3 acc=vec3(0.0);",
  "  for(int i=0;i<6;i++){",
  "    float fi=float(i);",
  "    float base=uCentre+(fi-2.5)*uPitch+sin(k1+uA[i])*uWave+sin(k2+uB[i])*uWaveFar;",
  "    float g=0.0;",
  "    for(int s=0;s<3;s++){",
  "      float dy=p.y-(base+sin(k3+uC[i]+float(s))*10.0);",
  "      float w=uW[i*3+s];",
  "      g+=exp(-dy*dy/(w*w));",
  "    }",
  "    vec3 col=i==5?uCol2*0.10:(i==1||i==3?uCol1*0.16:uCol0*0.16);",
  "    acc+=col*g;",
  "  }",
  // overexposure goes to blue-white, never to cyan: scale back to the hue, then lift towards paper
  "  vec3 c=uBg+acc*env*uLift;",
  "  float m=max(max(c.r,c.g),max(c.b,1.0));",
  "  c=mix(c/m,vec3(0.90,0.91,1.0),clamp((m-1.0)*0.32,0.0,0.55));",
  "  gl_FragColor=vec4(c,1.0);",
  "}"
].join("\n");

const UNIFORMS = ["uRes", "uView", "uOff", "uA", "uB", "uC", "uW", "uWave", "uWaveFar", "uCentre", "uPitch", "uCx",
  "uHalf", "uLift", "uBg", "uCol0", "uCol1", "uCol2", "uSwirlC", "uTwist", "uSwirl"];

function lerp(a, b, k) { return a + (b - a) * k; }
function ease(k, dt) { return 1 - Math.pow(1 - k, dt * 60); }
function bump(p, at, w) { const d = (p - at) / w; return Math.exp(-d * d); }
function heart(t, period) {
  if (!period) return 0;
  const u = (t / period) % 1;
  return 0.90 * bump(u, 0.38, 0.10) + 0.55 * bump(u, 0.52, 0.07) + 0.72 * bump(u, 0.70, 0.09);
}
const TAU = Math.PI * 2;
function wrap(x) { x = x % TAU; return x < 0 ? x + TAU : x; }

export const STATES = Object.keys(TARGETS);

export class Silk {
  constructor(canvas, opts = {}) {
    this.canvas = canvas;
    this.state = "resting";
    this.problem = false;
    this.quality = QUALITY[opts.quality] ? opts.quality : "full";
    this.levels = QUALITY[this.quality];
    this.level = 0;
    this.cssW = 1280;
    this.cssH = 720;
    this.cur = Object.assign({}, TARGETS.resting, { cx: MID, twistPhase: 0 });
    this.voice = 0;
    this.surge = 0;
    this.grey = 0;
    this.T = 0;
    this.last = 0;
    this.lastDraw = 0;
    this.intervals = [];
    this.fps = 0;
    this.frames = 0;
    this.fpsAt = 0;
    this.drawn = 0;          // frames drawn in all, for the page's own measurements
    this.renderer = "none";
    this.paused = false;
    this.settling = 0;       // "still": frames left to draw before drawing stops
    this.raf = 0;
    this.uA = new Float32Array(6);
    this.uB = new Float32Array(6);
    this.uC = new Float32Array(6);
    this.uW = new Float32Array(18);

    this.gl = null;
    if (!opts.noWebgl) this.initGl();
    if (!this.gl) this.init2d();
    this.setLevel(this.software && this.quality === "full" ? 3 : 0);
    this.loop = this.loop.bind(this);
    this.wake();
  }

  initGl() {
    let gl;
    try {
      gl = this.canvas.getContext("webgl", { alpha: false, antialias: false, depth: false, stencil: false,
        premultipliedAlpha: false, preserveDrawingBuffer: false, powerPreference: "low-power" });
    } catch (e) { gl = null; }
    if (!gl) return;
    const sh = (type, src) => {
      const s = gl.createShader(type);
      gl.shaderSource(s, src);
      gl.compileShader(s);
      if (!gl.getShaderParameter(s, gl.COMPILE_STATUS)) throw new Error(gl.getShaderInfoLog(s));
      return s;
    };
    try {
      const prog = gl.createProgram();
      gl.attachShader(prog, sh(gl.VERTEX_SHADER, VERT));
      gl.attachShader(prog, sh(gl.FRAGMENT_SHADER, FRAG));
      gl.linkProgram(prog);
      if (!gl.getProgramParameter(prog, gl.LINK_STATUS)) throw new Error(gl.getProgramInfoLog(prog));
      gl.useProgram(prog);
      const buf = gl.createBuffer();
      gl.bindBuffer(gl.ARRAY_BUFFER, buf);
      gl.bufferData(gl.ARRAY_BUFFER, new Float32Array([-1, -1, 3, -1, -1, 3]), gl.STATIC_DRAW);
      const loc = gl.getAttribLocation(prog, "a");
      gl.enableVertexAttribArray(loc);
      gl.vertexAttribPointer(loc, 2, gl.FLOAT, false, 0, 0);
      this.u = {};
      UNIFORMS.forEach((n) => { this.u[n] = gl.getUniformLocation(prog, n); });
      this.gl = gl;
      const info = gl.getExtension("WEBGL_debug_renderer_info");
      this.renderer = info ? String(gl.getParameter(info.UNMASKED_RENDERER_WEBGL)) : "webgl";
      this.software = /swiftshader|llvmpipe|softpipe|software/i.test(this.renderer);
      this.canvas.addEventListener("webglcontextlost", (e) => { e.preventDefault(); this.lost = true; });
      this.canvas.addEventListener("webglcontextrestored", () => {
        this.lost = false; this.gl = null; this.initGl(); this.setLevel(this.level); this.wake();
      });
    } catch (e) {
      this.gl = null;
      if (window.console) console.warn("silk: webgl unavailable, using the 2D fallback", e);
    }
  }

  init2d() {
    // A fresh canvas: one that has been asked for a webgl context cannot give a 2d one.
    const c = document.createElement("canvas");
    c.id = this.canvas.id;
    c.className = this.canvas.className + " fallback";
    c.setAttribute("aria-hidden", "true");
    this.canvas.parentNode.replaceChild(c, this.canvas);
    this.canvas = c;
    this.ctx = c.getContext("2d");
    this.renderer = "2d fallback";
    this.software = true;
  }

  setLevel(i) {
    this.level = Math.max(0, Math.min(this.levels.length - 1, i));
    const lv = this.levels[this.level];
    this.targetFps = lv.fps;
    const scale = Math.min(lv.scale, MAX_BUFFER / Math.max(1, this.cssW));
    this.bw = Math.max(16, Math.round(this.cssW * scale));
    this.bh = Math.max(9, Math.round(this.cssH * scale));
    this.canvas.width = this.bw;
    this.canvas.height = this.bh;
    if (this.gl) this.gl.viewport(0, 0, this.bw, this.bh);
    this.intervals.length = 0;
    this.settling = Math.max(this.settling, 2);
  }

  // The stage's size in CSS pixels.
  resize(w, h) {
    w = Math.max(1, Math.round(w)); h = Math.max(1, Math.round(h));
    if (w === this.cssW && h === this.cssH) return;
    this.cssW = w; this.cssH = h;
    this.setLevel(this.level);
    this.wake();
  }

  set(state) {
    if (!TARGETS[state] || state === this.state) return;
    this.state = state;
    this.settling = 150;
    this.wake();
  }

  setProblem(on) {
    on = !!on;
    if (on === this.problem) return;
    this.problem = on;
    this.settling = 150;
    this.wake();
  }

  // one surge of light when something is carried out
  pulse() { this.surge = 1; this.settling = Math.max(this.settling, 90); this.wake(); }

  pause(on) {
    this.paused = !!on;
    if (!this.paused) this.wake();
  }

  wake() {
    if (this.paused || this.raf) return;
    this.last = 0;
    this.lastDraw = 0;
    this.raf = requestAnimationFrame(this.loop);
  }

  loop(ms) {
    this.raf = 0;
    if (this.paused) return;
    const still = this.quality === "still";
    if (still && this.settling <= 0) return;   // nothing is changing: stop asking for frames
    this.raf = requestAnimationFrame(this.loop);
    const minGap = 1000 / this.targetFps - 2;
    if (this.lastDraw && ms - this.lastDraw < minGap) return;
    const dt = this.last ? Math.min(0.1, (ms - this.last) / 1000) : 1 / 60;
    if (this.lastDraw && !still) this.govern(ms - this.lastDraw);
    this.last = ms;
    this.lastDraw = ms;
    this.update(dt);
    if (still) this.settling--;
    if (this.lost) return;
    if (this.gl) this.drawGl(); else this.draw2d();
    this.drawn++;
    this.frames++;
    if (ms - this.fpsAt >= 1000) {
      this.fps = this.frames * 1000 / (ms - this.fpsAt);
      this.frames = 0;
      this.fpsAt = ms;
    }
  }

  // Step down the buffer size, then the frame rate, if frames keep arriving late.
  govern(gap) {
    const iv = this.intervals;
    iv.push(gap);
    if (iv.length < 120) return;
    let sum = 0;
    for (let i = 0; i < iv.length; i++) sum += iv[i];
    const mean = sum / iv.length;
    iv.length = 0;
    if (mean > (1000 / this.targetFps) * 1.45 && this.level < this.levels.length - 1) this.setLevel(this.level + 1);
  }

  update(dt) {
    const tg = TARGETS[this.state], c = this.cur;
    // "still" holds time: the ribbons keep their shape and only the state's settling moves them
    this.T += this.quality === "still" ? 0 : dt;
    const T = this.T;
    const k = ease(0.055, dt);
    const problem = this.problem || tg.grey > 0;
    c.half = lerp(c.half, tg.half, k);
    c.amp = lerp(c.amp, tg.amp, k);
    c.centre = lerp(c.centre, tg.centre, k);
    c.lift = lerp(c.lift, tg.lift * (this.problem ? 0.6 : 1), ease(tg.lift > c.lift ? 0.12 : 0.05, dt));
    // the flood lands in about a tenth of a second; it drains over about a second when the
    // result settles, faster when something new takes the stage
    const drain = this.state === "resting" ? 0.045 : 0.16;
    c.flood = lerp(c.flood, tg.flood, ease(tg.flood > c.flood ? 0.38 : drain, dt));
    c.swirl = lerp(c.swirl, tg.swirl, ease(tg.swirl > c.swirl ? 0.045 : 0.03, dt));
    // the eddy turns in and comes to rest; it never winds into a coil
    c.twistPhase = c.swirl > 0.01 ? Math.min(1.4, c.twistPhase + dt * 0.55) : 0;
    this.grey = lerp(this.grey, problem ? 1 : 0, ease(0.05, dt));
    c.cx = lerp(c.cx, MID + 110 * Math.sin(T * 0.05), k);

    let vT = 0;
    if (tg.voice) vT = Math.max(0, Math.min(1, 0.40 + 0.30 * Math.sin(T * 3.1) + 0.18 * Math.sin(T * 7.7 + 1.2) + 0.12 * Math.sin(T * 13.3 + 2.4)));
    this.voice = lerp(this.voice, vT, ease(0.12, dt));
    this.surge *= Math.exp(-dt / 0.45);
    const beat = heart(T, tg.beat);
    const amp = c.amp * (problem ? 0.6 : 1) * (1 + beat * 0.5 + this.voice * 0.9) + this.surge * 0.5;
    this.lift = c.lift * (1 + this.surge * 0.35);

    this.wave = 34 * (1 + amp * 1.4);
    this.waveFar = 26 * (1 + amp);
    this.pitch = 26 * (1 + amp * 0.55);
    for (let i = 0; i < 6; i++) {
      const s2 = 0.55 + i * 0.12;
      this.uA[i] = wrap(T * s2 + i);
      this.uB[i] = wrap(-T * s2 * 0.7 + i * 2);
      this.uC[i] = wrap(T * s2 * 1.6);
      for (let s = 0; s < 3; s++) {
        const lw = (18.8 + 14.1 * Math.sin(T * 0.6 + i + s)) * (1 + amp * 0.8) * 1.15;
        this.uW[i * 3 + s] = Math.sqrt(lw * lw + 17 * 17);
      }
    }
    this.twist = c.swirl * (1.7 + c.twistPhase);
    const f = c.flood;
    this.bg = [lerp(PLATE[0], SIGNAL[0], f), lerp(PLATE[1], SIGNAL[1], f), lerp(PLATE[2], SIGNAL[2], f)];
    const gr = this.grey * 0.85;
    this.cols = [0, 1, 2].map((j) => {
      const col = [lerp(ON_PLATE[j][0], ON_FLOOD[j][0], f), lerp(ON_PLATE[j][1], ON_FLOOD[j][1], f), lerp(ON_PLATE[j][2], ON_FLOOD[j][2], f)];
      const g = (col[0] + col[1] + col[2]) / 3 + 0.12;
      return [lerp(col[0], g, gr), lerp(col[1], g, gr), lerp(col[2], g, gr)];
    });
  }

  view() {
    const v = H * this.cssW / this.cssH;
    return { v, off: (1280 - v) / 2 };
  }

  drawGl() {
    const gl = this.gl, u = this.u, c = this.cur, vw = this.view();
    gl.uniform2f(u.uRes, this.bw, this.bh);
    gl.uniform1f(u.uView, vw.v);
    gl.uniform1f(u.uOff, vw.off);
    gl.uniform1fv(u.uA, this.uA);
    gl.uniform1fv(u.uB, this.uB);
    gl.uniform1fv(u.uC, this.uC);
    gl.uniform1fv(u.uW, this.uW);
    gl.uniform1f(u.uWave, this.wave);
    gl.uniform1f(u.uWaveFar, this.waveFar);
    gl.uniform1f(u.uCentre, c.centre);
    gl.uniform1f(u.uPitch, this.pitch);
    gl.uniform1f(u.uCx, c.cx);
    gl.uniform1f(u.uHalf, c.half);
    gl.uniform1f(u.uLift, this.lift);
    gl.uniform3fv(u.uBg, this.bg);
    gl.uniform3fv(u.uCol0, this.cols[0]);
    gl.uniform3fv(u.uCol1, this.cols[1]);
    gl.uniform3fv(u.uCol2, this.cols[2]);
    gl.uniform2f(u.uSwirlC, SWIRL_C[0], SWIRL_C[1]);
    gl.uniform1f(u.uTwist, this.twist);
    gl.uniform1f(u.uSwirl, c.swirl);
    gl.drawArrays(gl.TRIANGLES, 0, 3);
  }

  // The fallback strokes the same ribbons as polylines into a small 2D canvas and lets
  // the compositor blur it (CSS filter on the element).
  draw2d() {
    const ctx = this.ctx, c = this.cur, vw = this.view();
    const sx = this.bw / vw.v, sy = this.bh / H, ox = vw.off;
    const css = (v, a) => "rgba(" + Math.round(v[0] * 255) + "," + Math.round(v[1] * 255) + "," + Math.round(v[2] * 255) + "," + a + ")";
    ctx.setTransform(1, 0, 0, 1, 0, 0);
    ctx.globalCompositeOperation = "source-over";
    ctx.fillStyle = css(this.bg, 1);
    ctx.fillRect(0, 0, this.bw, this.bh);
    ctx.globalCompositeOperation = "lighter";
    ctx.lineCap = "round";
    const [scx, scy] = SWIRL_C;
    for (let i = 0; i < 6; i++) {
      const col = i === 5 ? this.cols[2] : (i === 1 || i === 3 ? this.cols[1] : this.cols[0]);
      const alpha = (i === 5 ? 0.10 : 0.16) * this.lift * 1.6;
      const g = ctx.createLinearGradient((c.cx - c.half * 1.6 - ox) * sx, 0, (c.cx + c.half * 1.6 - ox) * sx, 0);
      g.addColorStop(0, css(col, 0));
      g.addColorStop(0.5, css(col, Math.min(1, alpha)));
      g.addColorStop(1, css(col, 0));
      ctx.strokeStyle = g;
      for (let s = 0; s < 3; s++) {
        ctx.beginPath();
        for (let x = ox - 40; x <= ox + vw.v + 40; x += 32) {
          const y = c.centre + (i - 2.5) * this.pitch + Math.sin(x * 0.006 + this.uA[i]) * this.wave +
            Math.sin(x * 0.0026 + this.uB[i]) * this.waveFar + Math.sin(x * 0.0115 + this.uC[i] + s) * 10;
          let px = x, py = y;
          if (c.swirl > 0.01) {
            const dx = x - scx, dy = y - scy;
            const fall = Math.exp(-(dx * dx + dy * dy) / 135200);
            const an = -this.twist * fall, sc = 1 / (1 + 0.42 * c.swirl * fall);
            px = scx + (Math.cos(an) * dx - Math.sin(an) * dy) * sc;
            py = scy + (Math.sin(an) * dx + Math.cos(an) * dy) * sc;
          }
          if (x === ox - 40) ctx.moveTo((px - ox) * sx, py * sy); else ctx.lineTo((px - ox) * sx, py * sy);
        }
        ctx.lineWidth = Math.max(1, this.uW[i * 3 + s] * 1.2 * sy);
        ctx.stroke();
      }
    }
  }
}
