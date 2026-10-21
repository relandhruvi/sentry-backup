import { useEffect, useRef } from "react";

type Props = {
  className?: string;
  particleCount?: number;
  accent?: string;
  radius?: number;
  viewport?: boolean;
};

type P = {
  x: number;
  y: number;
  vx: number;
  vy: number;
  bx: number;
  by: number;
  accent: boolean;
};

export function AtmosphericFriction({
  className = "",
  particleCount = 900,
  accent = "#CC8066",
  radius = 220,
  viewport = false,
}: Props) {
  const canvasRef = useRef<HTMLCanvasElement>(null);

  useEffect(() => {
    const canvas = canvasRef.current;
    const parent = canvas?.parentElement;
    if (!canvas || !parent) return;
    const ctx = canvas.getContext("2d");
    if (!ctx) return;

    const reduce = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
    const touch = window.matchMedia("(pointer: coarse)").matches;
    if (reduce || touch) return;

    const dpr = Math.min(window.devicePixelRatio || 1, 2);
    let w = 0;
    let h = 0;
    let particles: P[] = [];
    const mouse = { x: -9999, y: -9999, active: false };
    let visible = false;
    let running = false;
    let raf = 0;

    const seed = () => {
      const count = Math.round(particleCount * Math.min(1, (w * h) / (1440 * 800)));
      particles = Array.from({ length: Math.max(count, 200) }, () => {
        const angle = Math.random() * Math.PI * 2;
        const speed = 0.15 + Math.random() * 0.35;
        const bx = Math.cos(angle) * speed;
        const by = Math.sin(angle) * speed;
        return {
          x: Math.random() * w,
          y: Math.random() * h,
          vx: bx,
          vy: by,
          bx,
          by,
          accent: Math.random() < 0.18,
        };
      });
    };

    const resize = () => {
      const r = parent.getBoundingClientRect();
      w = viewport ? window.innerWidth : r.width;
      h = viewport ? window.innerHeight : r.height;
      canvas.width = w * dpr;
      canvas.height = h * dpr;
      canvas.style.width = `${w}px`;
      canvas.style.height = `${h}px`;
      ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
      seed();
    };

    const onMove = (e: PointerEvent) => {
      const r = viewport ? canvas.getBoundingClientRect() : parent.getBoundingClientRect();
      const x = e.clientX - r.left;
      const y = e.clientY - r.top;
      mouse.active = x >= 0 && y >= 0 && x <= r.width && y <= r.height;
      mouse.x = x;
      mouse.y = y;
    };
    const onLeave = () => {
      mouse.active = false;
    };

    const tick = () => {
      if (!visible) {
        running = false;
        return;
      }
      ctx.clearRect(0, 0, w, h);

      for (const p of particles) {
        p.vx += (p.bx - p.vx) * 0.04;
        p.vy += (p.by - p.vy) * 0.04;

        if (mouse.active) {
          const dx = mouse.x - p.x;
          const dy = mouse.y - p.y;
          const d = Math.hypot(dx, dy) || 1;
          if (d < radius) {
            const f = 1 - d / radius;
            p.vx += (-dy / d) * f * 0.9 + (dx / d) * f * 0.12;
            p.vy += (dx / d) * f * 0.9 + (dy / d) * f * 0.12;
          }
        }

        p.x += p.vx;
        p.y += p.vy;

        if (p.x < 0) p.x = w;
        else if (p.x > w) p.x = 0;
        if (p.y < 0) p.y = h;
        else if (p.y > h) p.y = 0;

        const speed = Math.hypot(p.vx, p.vy);
        ctx.strokeStyle = p.accent ? accent : "#ffffff";
        ctx.globalAlpha = Math.min(0.9, 0.35 + speed * 0.3);
        if (speed > 0.35) {
          ctx.lineWidth = 1;
          ctx.beginPath();
          ctx.moveTo(p.x - p.vx * 3, p.y - p.vy * 3);
          ctx.lineTo(p.x, p.y);
          ctx.stroke();
        } else {
          ctx.fillStyle = ctx.strokeStyle;
          ctx.fillRect(p.x, p.y, 1.5, 1.5);
        }
      }
      ctx.globalAlpha = 1;
      raf = requestAnimationFrame(tick);
    };

    const io = new IntersectionObserver(([entry]) => {
      visible = entry.isIntersecting;
      if (visible && !running) {
        running = true;
        raf = requestAnimationFrame(tick);
      } else if (!visible && running) {
        cancelAnimationFrame(raf);
        running = false;
      }
    });
    io.observe(parent);

    const ro = new ResizeObserver(resize);
    ro.observe(parent);
    if (viewport) window.addEventListener("resize", resize);
    resize();

    window.addEventListener("pointermove", onMove, { passive: true });
    parent.addEventListener("pointerleave", onLeave);
    return () => {
      cancelAnimationFrame(raf);
      io.disconnect();
      ro.disconnect();
      if (viewport) window.removeEventListener("resize", resize);
      window.removeEventListener("pointermove", onMove);
      parent.removeEventListener("pointerleave", onLeave);
    };
  }, [particleCount, accent, radius, viewport]);

  return (
    <canvas
      ref={canvasRef}
      aria-hidden="true"
      className={`pointer-events-none z-0 ${viewport ? "wl-atmospheric-viewport" : "absolute inset-0"} ${className}`}
    />
  );
}

export default AtmosphericFriction;
