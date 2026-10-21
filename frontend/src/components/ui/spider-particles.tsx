// Built using Hyperiux Vault: https://vault.hyperiux.com


import { useEffect, useRef, useState } from "react";
import * as THREE from "three";

/* ---- inlined from ./createSuspendedRaf ---- */
const DEFAULT_ROOT_MARGIN = "256px";

type RafRoot = Element | null | { current: Element | null } | (() => Element | null);

function resolveElement(root: RafRoot): Element | null {
  if (!root) return null;
  if (typeof root === "function") return root() ?? null;
  if (typeof root === "object" && "current" in root) return root.current ?? null;
  return root;
}

interface VisibilityGateOptions {
  root?: RafRoot;
  rootMargin?: string;
  threshold?: number;
  observeTab?: boolean;
  observeOffscreen?: boolean;
  onChange?: (active: boolean) => void;
}

interface VisibilityGate {
  readonly isActive: boolean;
  observe: (nextRoot?: RafRoot) => void;
  destroy: () => void;
}

function createVisibilityGate({
  root = null,
  rootMargin = DEFAULT_ROOT_MARGIN,
  threshold = 0,
  observeTab = true,
  observeOffscreen = true,
  onChange,
}: VisibilityGateOptions = {}): VisibilityGate {
  let tabVisible =
    typeof document === "undefined" ? true : !document.hidden;
  // Match border-beam: assume onscreen until the observer reports otherwise.
  let onscreen = true;
  let destroyed = false;
  let observer: IntersectionObserver | null = null;

  const isActive = () => {
    if (destroyed) return false;
    if (observeTab && !tabVisible) return false;
    if (observeOffscreen && resolveElement(root) && !onscreen) return false;
    return true;
  };

  let lastActive = isActive();

  const emit = () => {
    if (destroyed) return;
    const next = isActive();
    if (next === lastActive) return;
    lastActive = next;
    onChange?.(next);
  };

  const onVisibilityChange = () => {
    tabVisible = !document.hidden;
    emit();
  };

  if (observeTab && typeof document !== "undefined") {
    document.addEventListener("visibilitychange", onVisibilityChange);
  }

  const bindObserver = () => {
    if (!observeOffscreen || typeof IntersectionObserver === "undefined") {
      return;
    }

    const el = resolveElement(root);
    if (!el) return;

    observer = new IntersectionObserver(
      (entries) => {
        for (const entry of entries) {
          onscreen = entry.isIntersecting;
        }
        emit();
      },
      { rootMargin, threshold },
    );

    observer.observe(el);
  };

  bindObserver();

  return {
    /** Whether the animation should currently run. */
    get isActive() {
      return isActive();
    },

    /**
     * Re-bind IntersectionObserver after the root element mounts late
     * (e.g. ref not ready on first call). Safe to call multiple times.
     */
    observe(nextRoot?: RafRoot) {
      if (destroyed) return;
      if (nextRoot != null) root = nextRoot;
      if (observer) {
        observer.disconnect();
        observer = null;
      }
      onscreen = true;
      bindObserver();
      emit();
    },

    destroy() {
      if (destroyed) return;
      destroyed = true;
      if (observeTab && typeof document !== "undefined") {
        document.removeEventListener("visibilitychange", onVisibilityChange);
      }
      if (observer) {
        observer.disconnect();
        observer = null;
      }
    },
  };
}

interface SuspendedRafOptions {
  onFrame: (time: number) => void;
  root?: RafRoot;
  rootMargin?: string;
  threshold?: number;
  observeTab?: boolean;
  observeOffscreen?: boolean;
}

interface SuspendedRaf {
  start: () => void;
  stop: () => void;
  readonly isRunning: boolean;
  readonly isActive: boolean;
  observe: (nextRoot?: RafRoot) => void;
  destroy: () => void;
}

/**
 * Owns a requestAnimationFrame loop that auto-pauses when the tab is hidden
 * or the root element is offscreen.
 */
function createSuspendedRaf({
  onFrame,
  root = null,
  rootMargin = DEFAULT_ROOT_MARGIN,
  threshold = 0,
  observeTab = true,
  observeOffscreen = true,
}: SuspendedRafOptions): SuspendedRaf {
  if (typeof onFrame !== "function") {
    throw new TypeError("createSuspendedRaf: onFrame is required");
  }

  let rafId: number | null = null;
  let running = false;
  let destroyed = false;

  const stopRaf = () => {
    if (rafId != null) {
      cancelAnimationFrame(rafId);
      rafId = null;
    }
  };

  const tick = (time: number) => {
    rafId = null;
    if (destroyed || !running || !gate.isActive) return;
    onFrame(time);
    if (!destroyed && running && gate.isActive) {
      rafId = requestAnimationFrame(tick);
    }
  };

  const sync = () => {
    if (destroyed) return;
    if (running && gate.isActive) {
      if (rafId == null) {
        rafId = requestAnimationFrame(tick);
      }
    } else {
      stopRaf();
    }
  };

  const gate = createVisibilityGate({
    root,
    rootMargin,
    threshold,
    observeTab,
    observeOffscreen,
    onChange: sync,
  });

  return {
    /** Start (or resume) the loop when visibility allows. */
    start() {
      if (destroyed) return;
      running = true;
      sync();
    },

    /** Stop requesting frames (visibility listeners stay attached until destroy). */
    stop() {
      running = false;
      stopRaf();
    },

    /** Whether the caller has started the loop (may still be paused by visibility). */
    get isRunning() {
      return running;
    },

    /** Whether a frame is currently allowed to schedule. */
    get isActive() {
      return gate.isActive;
    },

    /** Re-attach offscreen observer to a (new) root element. */
    observe(nextRoot?: RafRoot) {
      gate.observe(nextRoot);
      sync();
    },

    /** Tear down listeners and cancel any pending frame. */
    destroy() {
      if (destroyed) return;
      destroyed = true;
      running = false;
      stopRaf();
      gate.destroy();
    },
  };
}
/* ---- end inlined helper ---- */

//  Constants
const MOUSE_OFFSCREEN = -9999;
const MOUSE_THRESHOLD = -9000; // anything above this = mouse is on screen

const LERP_SPEED = 0.1;
const FADE_SPEED = 0.04;

const REDUCED_MOTION_FACTOR = 0.6;

// GLSL Shaders

const PARTICLE_VERT = /* glsl */ `
  uniform float uSize;
  uniform vec2  uMouse;
  uniform float uSpotlightRadius;

  void main() {
    vec4  mvPos = modelViewMatrix * vec4(position, 1.0);
    float dist  = distance(position.xy, uMouse);
    float scale = dist < uSpotlightRadius
      ? 0.18 + 0.82 * (1.0 - (dist / uSpotlightRadius))
      : 0.18;

    gl_PointSize = uSize * scale;
    gl_Position  = projectionMatrix * mvPos;
  }
`;

const CURSOR_VERT = /* glsl */ `
  uniform float uSize;

  void main() {
    vec4 mvPos   = modelViewMatrix * vec4(position, 1.0);
    gl_PointSize = uSize;
    gl_Position  = projectionMatrix * mvPos;
  }
`;

// Shared by both particle dots and the cursor dot
const POINT_FRAG = /* glsl */ `
  uniform vec3  uColor;
  uniform vec3  uGlow;
  uniform bool  uGlowEnabled;
  uniform float uAlpha;

  void main() {
    vec2  uv = gl_PointCoord - 0.5;
    float d  = length(uv);
    if (d > 0.5) discard;

    if (uGlowEnabled) {
      float core  = smoothstep(0.5, 0.0, d);
      float glow  = smoothstep(0.5, 0.1, d) * 0.6;
      vec3  col   = mix(uGlow, uColor, core);
      float alpha = (core + glow) * uAlpha;
      gl_FragColor = vec4(col, alpha);
    } else {
      float alpha = smoothstep(0.5, 0.45, d) * uAlpha;
      gl_FragColor = vec4(uColor, alpha);
    }
  }
`;

export interface SpiderParticlesProps {
  particleCount?: number;
  gridGap?: number;
  particleSize?: number;
  mouseConnectDist?: number;
  spotlightRadius?: number;
  showWeb?: boolean;
  particlesGlow?: boolean;
  glowColor?: string | number;
  particleColor?: string | number;
  webColor?: string | number;
  centerColor?: string | number;
  /** Page background behind the canvas. */
  bgColor?: string;
  /** Heading in the overlay. Empty string hides the whole HUD text block. */
  title?: string;
  /** Sub-line under the heading. */
  subtitle?: string;
  /** Show the corner readouts (status, stats, position, hint). */
  showHud?: boolean;
  className?: string;
}

// Component
export default function SpiderParticles({
  particleCount = 180,
  gridGap = 0,
  particleSize = 20.0,
  mouseConnectDist = 160,
  spotlightRadius = 300,
  showWeb = true,
  particlesGlow = false,
  glowColor = 0xffffff,
  particleColor = 0xffffff,
  webColor = 0xffffff,
  centerColor = 0xffffff,
  bgColor = "#000000",
  title = "Spider Web",
  subtitle = "Interactive particle field",
  showHud = true,
  className = "",
}: SpiderParticlesProps) {
  const mountRef = useRef<HTMLDivElement | null>(null);
  const [active, setActive] = useState(false);
  const [pos, setPos] = useState({ x: 0, y: 0 });
  // Whether cursor interaction should be available. Driven by pointer type,
  // not viewport width, so a narrow-but-mouse-driven frame (e.g. an embed
  // preview) still gets the full effect - only real touch devices fall back.
  const [isCoarsePointer, setIsCoarsePointer] = useState(() =>
    typeof window !== "undefined" && window.matchMedia("(pointer: coarse)").matches,
  );

  useEffect(() => {
    const mediaQuery = window.matchMedia("(pointer: coarse)");
    const update = () => setIsCoarsePointer(mediaQuery.matches);
    update();
    mediaQuery.addEventListener("change", update);
    return () => mediaQuery.removeEventListener("change", update);
  }, []);

  useEffect(() => {
    const mount = mountRef.current;
    if (!mount || isCoarsePointer) return;

    let width = mount.clientWidth || window.innerWidth;
    let height = mount.clientHeight || window.innerHeight;

    const _glowColor     = new THREE.Color(glowColor);
    const _particleColor = new THREE.Color(particleColor);
    const _webColor      = new THREE.Color(webColor);
    const _centerColor   = new THREE.Color(centerColor);



    const renderer = new THREE.WebGLRenderer({ antialias: true, alpha: true });
    renderer.setPixelRatio(Math.min(window.devicePixelRatio, 2));
    renderer.setSize(width, height);
    renderer.setClearColor(0x000000, 0);
    mount.appendChild(renderer.domElement);

    const scene = new THREE.Scene();
    const camera = new THREE.OrthographicCamera(
      -width / 2, width / 2, height / 2, -height / 2, -500, 500,
    );
    camera.position.z = 1;

    // Mouse State
    const mouse       = new THREE.Vector2(MOUSE_OFFSCREEN, MOUSE_OFFSCREEN);
    const smoothMouse = new THREE.Vector2(MOUSE_OFFSCREEN, MOUSE_OFFSCREEN);
    let mouseEntryAlpha = 0;
    let mousePresent    = false;
    let mouseJustEntered = false; // snap smoothMouse on the first frame after cursor enters

    // Pointer type, not viewport width - a narrow but mouse-driven frame
    // (e.g. an embed preview) should still track the cursor.
    const isDesktop = () => !window.matchMedia("(pointer: coarse)").matches;

    const onMove = (e: PointerEvent) => {
      if (!isDesktop()) return;
      const rect = mount.getBoundingClientRect();
      const inside = e.clientX >= rect.left && e.clientX <= rect.right && e.clientY >= rect.top && e.clientY <= rect.bottom;
      if (!inside) {
        if (mousePresent) onLeave();
        return;
      }
      mouse.set(
        e.clientX - rect.left - width / 2,
        -(e.clientY - rect.top - height / 2),
      );
      if (showHud) setPos({ x: e.clientX - rect.left, y: e.clientY - rect.top });
      if (!mousePresent) {
        mouseJustEntered = true;
        mousePresent = true;
        setActive(true);
      }
    };

    const onLeave = () => {
      if (!isDesktop()) return;
      mousePresent     = false;
      mouseJustEntered = false;
      setActive(false);
    };

    // Listen on the window because this canvas is a pointer-transparent layer
    // behind the hero content. Pointer input continues to reach links/buttons.
    window.addEventListener("pointermove", onMove);

    // Grid Layout

    let cols: number, rows: number, actualCount: number, spacingX: number, spacingY: number;

    if (gridGap > 0) {
      // Explicit grid: cells are gridGap pixels apart
      cols        = Math.max(1, Math.floor(width  / gridGap));
      rows        = Math.max(1, Math.floor(height / gridGap));
      actualCount = cols * rows;
      spacingX = spacingY = gridGap;
    } else {
      // Auto grid: fit particleCount evenly, respecting aspect ratio
      actualCount  = particleCount;
      const aspect = width / height;
      rows    = Math.max(1, Math.round(Math.sqrt(actualCount / aspect)));
      cols    = Math.ceil(actualCount / rows);
      spacingX = width  / cols;
      spacingY = height / rows;
    }

    const positions = new Float32Array(actualCount * 3);
    const driftX = new Float32Array(actualCount);
    const driftY = new Float32Array(actualCount);
    const velocityX = new Float32Array(actualCount);
    const velocityY = new Float32Array(actualCount);

    for (let i = 0; i < actualCount; i++) {
      const c = i % cols;
      const r = Math.floor(i / cols);
      positions[i * 3]     = (c + 0.5) * spacingX - width  / 2;
      positions[i * 3 + 1] = (r + 0.5) * spacingY - height / 2;
      positions[i * 3 + 2] = 0;
      const angle = Math.random() * Math.PI * 2;
      const speed = 0.15 + Math.random() * 0.35;
      driftX[i] = velocityX[i] = Math.cos(angle) * speed;
      driftY[i] = velocityY[i] = Math.sin(angle) * speed;
    }

    //  Particle Points

    const particleGeo = new THREE.BufferGeometry();
    particleGeo.setAttribute("position", new THREE.BufferAttribute(positions, 3));

    const particleMat = new THREE.ShaderMaterial({
      uniforms: {
        uColor:          { value: _particleColor },
        uGlow:           { value: _glowColor },
        uSize:           { value: particleSize * Math.min(window.devicePixelRatio, 2) },
        uMouse:          { value: new THREE.Vector2(MOUSE_OFFSCREEN, MOUSE_OFFSCREEN) },
        uSpotlightRadius: { value: spotlightRadius },
        uGlowEnabled:    { value: particlesGlow },
        uAlpha:          { value: 0.34 },
      },
      vertexShader:   PARTICLE_VERT,
      fragmentShader: POINT_FRAG,
      transparent:  true,
      depthWrite:   false,
      blending:     THREE.AdditiveBlending,
    });

    const particles = new THREE.Points(particleGeo, particleMat);
    scene.add(particles);

    // Cursor Dot

    const cursorGeo = new THREE.BufferGeometry();
    cursorGeo.setAttribute(
      "position",
      new THREE.BufferAttribute(new Float32Array([0, 0, 0]), 3),
    );

    const cursorMat = new THREE.ShaderMaterial({
      uniforms: {
        uColor:       { value: _centerColor },
        uGlow:        { value: _glowColor },
        uSize:        { value: particleSize * Math.min(window.devicePixelRatio, 2) },
        uGlowEnabled: { value: particlesGlow },
        uAlpha:       { value: 0.0 },
      },
      vertexShader:   CURSOR_VERT,
      fragmentShader: POINT_FRAG,
      transparent:  true,
      depthWrite:   false,
      blending:     THREE.AdditiveBlending,
    });

    const cursorPoint = new THREE.Points(cursorGeo, cursorMat);
    scene.add(cursorPoint);

    //Web Lines
    const mouseLinePositions = new Float32Array(actualCount * 6);
    const mouseLineColors    = new Float32Array(actualCount * 6);

    const mouseLineGeo = new THREE.BufferGeometry();
    mouseLineGeo.setAttribute("position", new THREE.BufferAttribute(mouseLinePositions, 3));
    mouseLineGeo.setAttribute("color",    new THREE.BufferAttribute(mouseLineColors,    3));

    const mouseLineMat = new THREE.LineBasicMaterial({
      vertexColors: true,
      transparent:  true,
      opacity:      1,
      blending:     THREE.AdditiveBlending,
      depthWrite:   false,
    });

    const mouseLines = new THREE.LineSegments(mouseLineGeo, mouseLineMat);
    scene.add(mouseLines);

    // A quiet ambient mesh keeps the nodes connected while the cursor is away.
    const ambientLineCapacity = (actualCount * (actualCount - 1)) / 2;
    const ambientLinePositions = new Float32Array(ambientLineCapacity * 6);
    const ambientLineColors = new Float32Array(ambientLineCapacity * 6);
    const ambientLineGeo = new THREE.BufferGeometry();
    ambientLineGeo.setAttribute("position", new THREE.BufferAttribute(ambientLinePositions, 3));
    ambientLineGeo.setAttribute("color", new THREE.BufferAttribute(ambientLineColors, 3));
    const ambientLineMat = new THREE.LineBasicMaterial({
      vertexColors: true,
      transparent: true,
      opacity: 1,
      blending: THREE.AdditiveBlending,
      depthWrite: false,
    });
    const ambientLines = new THREE.LineSegments(ambientLineGeo, ambientLineMat);
    scene.add(ambientLines);

    // Resize

    const onResize = () => {
      width  = mount.clientWidth;
      height = mount.clientHeight;
      renderer.setSize(width, height);
      camera.left   = -width  / 2;
      camera.right  =  width  / 2;
      camera.top    =  height / 2;
      camera.bottom = -height / 2;
      camera.updateProjectionMatrix();
    };
    window.addEventListener("resize", onResize);

    let reduceMotion =
      window.matchMedia?.("(prefers-reduced-motion: reduce)")?.matches ?? false;
    const reduceMotionMq = window.matchMedia?.(
      "(prefers-reduced-motion: reduce)"
    );
    const onReduceMotionChange = (event: MediaQueryListEvent) => {
      reduceMotion = event.matches;
    };
    reduceMotionMq?.addEventListener?.("change", onReduceMotionChange);

    //  Animation Loop

    const loop = createSuspendedRaf({
      root: mount,
      onFrame: () => {

      const lerpSpeed   = reduceMotion ? LERP_SPEED * REDUCED_MOTION_FACTOR       : LERP_SPEED;
      const fadeSpeed   = reduceMotion ? FADE_SPEED * REDUCED_MOTION_FACTOR       : FADE_SPEED;
      const spotlightR  = reduceMotion ? spotlightRadius * REDUCED_MOTION_FACTOR  : spotlightRadius;
      const connectDist = reduceMotion ? mouseConnectDist * REDUCED_MOTION_FACTOR : mouseConnectDist;

      // Keep the web gently in motion at rest; the cursor pulls nearby nodes in.
      for (let i = 0; i < actualCount; i++) {
        let x = positions[i * 3];
        let y = positions[i * 3 + 1];
        velocityX[i] += (driftX[i] - velocityX[i]) * 0.025;
        velocityY[i] += (driftY[i] - velocityY[i]) * 0.025;
        if (mousePresent) {
          const dx = smoothMouse.x - x;
          const dy = smoothMouse.y - y;
          const d = Math.hypot(dx, dy) || 1;
          if (d < spotlightR) {
            const force = (1 - d / spotlightR) * 0.025;
            velocityX[i] += (dx / d) * force;
            velocityY[i] += (dy / d) * force;
          }
        }
        x += velocityX[i];
        y += velocityY[i];
        if (x < -width / 2) x += width;
        else if (x > width / 2) x -= width;
        if (y < -height / 2) y += height;
        else if (y > height / 2) y -= height;
        positions[i * 3] = x;
        positions[i * 3 + 1] = y;
      }
      particleGeo.attributes.position.needsUpdate = true;

      // Fade the entire effect in/out as cursor enters or leaves
      mouseEntryAlpha = mousePresent
        ? Math.min(1, mouseEntryAlpha + fadeSpeed)
        : Math.max(0, mouseEntryAlpha - fadeSpeed);

      // Snap smoothMouse on the first frame, lerp every frame after
      if (mousePresent && mouse.x > MOUSE_THRESHOLD) {
        if (mouseJustEntered) {
          smoothMouse.copy(mouse); // instant snap - avoids lerp drift from previous position
          mouseJustEntered = false;
        } else {
          smoothMouse.x += (mouse.x - smoothMouse.x) * lerpSpeed;
          smoothMouse.y += (mouse.y - smoothMouse.y) * lerpSpeed;
        }
      } else if (!mousePresent && mouseEntryAlpha <= 0) {
        smoothMouse.set(MOUSE_OFFSCREEN, MOUSE_OFFSCREEN);
      }

      // Sync uniforms
      particleMat.uniforms.uMouse.value.copy(smoothMouse);
      particleMat.uniforms.uAlpha.value = 0.34 + mouseEntryAlpha * 0.48;
      cursorMat.uniforms.uAlpha.value   = mouseEntryAlpha;
      particleMat.uniforms.uSpotlightRadius.value = spotlightR;

      // Move cursor dot
      if (smoothMouse.x > MOUSE_THRESHOLD) {
        cursorPoint.position.set(smoothMouse.x, smoothMouse.y, 0);
        cursorPoint.visible = true;
      } else {
        cursorPoint.visible = false;
      }

      // Build web lines - one segment per nearby particle
      let mIdx = 0;

      if (showWeb && smoothMouse.x > MOUSE_THRESHOLD && mouseEntryAlpha > 0) {
        for (let i = 0; i < actualCount; i++) {
          const px = positions[i * 3];
          const py = positions[i * 3 + 1];
          const dx = px - smoothMouse.x;
          const dy = py - smoothMouse.y;
          const d  = Math.sqrt(dx * dx + dy * dy);

          if (d >= connectDist) continue; // outside web radius, skip

          const alpha = (1 - d / connectDist) * 0.85 * mouseEntryAlpha;
          const si    = mIdx * 6;

          // Line start = cursor position
          mouseLinePositions[si]     = smoothMouse.x;
          mouseLinePositions[si + 1] = smoothMouse.y;
          mouseLinePositions[si + 2] = 0;

          // Line end = particle position
          mouseLinePositions[si + 3] = px;
          mouseLinePositions[si + 4] = py;
          mouseLinePositions[si + 5] = 0;

          // Alpha is encoded per-vertex in RGB (additive blending, no real alpha channel)
          mouseLineColors[si]     = _webColor.r;
          mouseLineColors[si + 1] = _webColor.g;
          mouseLineColors[si + 2] = _webColor.b;
          mouseLineColors[si + 3] = _webColor.r * alpha;
          mouseLineColors[si + 4] = _webColor.g * alpha;
          mouseLineColors[si + 5] = _webColor.b * alpha;

          mIdx++;
        }
      }

      mouseLineGeo.setDrawRange(0, mIdx * 2);
      mouseLineGeo.attributes.position.needsUpdate = true;
      mouseLineGeo.attributes.color.needsUpdate    = true;

      let aIdx = 0;
      const ambientDist = Math.min(connectDist * 0.8, 120);
      for (let i = 0; i < actualCount; i++) {
        const x1 = positions[i * 3];
        const y1 = positions[i * 3 + 1];
        for (let j = i + 1; j < actualCount; j++) {
          const x2 = positions[j * 3];
          const y2 = positions[j * 3 + 1];
          const dx = x2 - x1;
          const dy = y2 - y1;
          const d = Math.hypot(dx, dy);
          if (d >= ambientDist) continue;
          const proximity = 1 - d / ambientDist;
          let alpha = 0.12 * proximity;
          if (smoothMouse.x > MOUSE_THRESHOLD) {
            const mouseDist = Math.min(Math.hypot(x1 - smoothMouse.x, y1 - smoothMouse.y), Math.hypot(x2 - smoothMouse.x, y2 - smoothMouse.y));
            if (mouseDist < connectDist) alpha += 0.42 * (1 - mouseDist / connectDist) * proximity;
          }
          const si = aIdx * 6;
          ambientLinePositions[si] = x1;
          ambientLinePositions[si + 1] = y1;
          ambientLinePositions[si + 2] = 0;
          ambientLinePositions[si + 3] = x2;
          ambientLinePositions[si + 4] = y2;
          ambientLinePositions[si + 5] = 0;
          ambientLineColors[si] = _webColor.r * alpha;
          ambientLineColors[si + 1] = _webColor.g * alpha;
          ambientLineColors[si + 2] = _webColor.b * alpha;
          ambientLineColors[si + 3] = _webColor.r * alpha;
          ambientLineColors[si + 4] = _webColor.g * alpha;
          ambientLineColors[si + 5] = _webColor.b * alpha;
          aIdx++;
        }
      }
      ambientLineGeo.setDrawRange(0, aIdx * 2);
      ambientLineGeo.attributes.position.needsUpdate = true;
      ambientLineGeo.attributes.color.needsUpdate = true;

      renderer.render(scene, camera);
      },
    });

    loop.start();

    //  Cleanup

    return () => {
      reduceMotionMq?.removeEventListener?.("change", onReduceMotionChange);
      loop.destroy();
      window.removeEventListener("resize", onResize);
      if (mount) {
        window.removeEventListener("pointermove", onMove);
        if (mount.contains(renderer.domElement)) {
          mount.removeChild(renderer.domElement);
        }
      }
      renderer.dispose();
      particleGeo.dispose();
      particleMat.dispose();
      cursorGeo.dispose();
      cursorMat.dispose();
      mouseLineGeo.dispose();
      mouseLineMat.dispose();
      ambientLineGeo.dispose();
      ambientLineMat.dispose();
    };
  }, [
    particleCount,
    gridGap,
    particleSize,
    mouseConnectDist,
    spotlightRadius,
    showWeb,
    particlesGlow,
    glowColor,
    particleColor,
    webColor,
    centerColor,
    isCoarsePointer,
  ]);

  // UI

  const stats = [
    { label: "Particles", value: particleCount },
    { label: "Radius",    value: spotlightRadius },
    { label: "Reach",     value: mouseConnectDist },
  ];

  return (
    <div
      ref={mountRef}
      style={{ backgroundColor: bgColor }}
      className={`h-full w-full overflow-hidden ${className}`}
      aria-hidden="true"
    >
      {isCoarsePointer ? <div className="wl-spider-fallback absolute inset-0" /> : null}
      {showHud && !isCoarsePointer ? <div className="absolute inset-0 z-10 pointer-events-none flex flex-col justify-between p-10">

        {/* Top row */}
        <div className="flex items-start pt-15 justify-between">
          <div className="flex flex-col gap-1.5">
            <div className={`flex items-center gap-2 ${isCoarsePointer || !showHud ? "hidden" : ""}`}>
              <span
                className={`w-1.5 h-1.5 rounded-full transition-all duration-500 ${
                  active ? "bg-emerald-400 shadow-[0_0_8px_#34d399]" : "bg-white/20"
                }`}
              />
              <span
                className={`text-sm tracking-widest uppercase transition-colors duration-500 ${
                  active ? "text-white/50" : "text-white/20"
                }`}
              >
                {active ? "Tracking" : "Idle"}
              </span>
            </div>
            {title ? (
              <h1 className="text-4xl font-light text-white/90 tracking-tight leading-none">
                {title}
              </h1>
            ) : null}
            {subtitle ? (
              <p className="text-md text-white/30 tracking-wide">
                {subtitle}
              </p>
            ) : null}
          </div>

          <div className={`flex gap-6 ${isCoarsePointer || !showHud ? "hidden" : ""}`}>
            {stats.map(({ label, value }) => (
              <div key={label} className="flex flex-col gap-0.5 text-right">
                <span className="text-sm uppercase tracking-widest text-white/25">
                  {label}
                </span>
                <span className="text-lg font-light text-white/60 tabular-nums">
                  {value}
                </span>
              </div>
            ))}
          </div>
        </div>

        {/* Bottom row */}
        <div className={`flex items-end justify-between ${isCoarsePointer || !showHud ? "hidden" : ""}`}>
          <div
            className={`transition-opacity duration-700 ${active ? "opacity-0" : "opacity-40"}`}
          >
            <p className="text-sm text-white tracking-widest uppercase">
              Move your cursor to explore
            </p>
            <div className="mt-1.5 w-8 h-px bg-white/20" />
          </div>

          <div
            className={`flex flex-col gap-1 text-right transition-opacity duration-500 ${active ? "opacity-100" : "opacity-0"}`}
          >
            <span className="text-sm uppercase tracking-widest text-white/25">
              Position
            </span>
            <span className="font-mono text-md text-white/50 tracking-wider">
              {Math.round(pos.x)} · {Math.round(pos.y)}
            </span>
          </div>
        </div>

      </div> : null}
    </div>
  );
}
