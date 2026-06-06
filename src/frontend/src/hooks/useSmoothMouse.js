import { useEffect, useRef, useState } from 'react';

/**
 * Smoothly tracks pointer position as normalized 0–1 coordinates.
 * Respects prefers-reduced-motion (snaps without animation).
 */
export function useSmoothMouse(smoothing = 0.07) {
    const [mouse, setMouse] = useState({ x: 0.5, y: 0.5, active: false });
    const target = useRef({ x: 0.5, y: 0.5, active: false });
    const current = useRef({ x: 0.5, y: 0.5 });
    const raf = useRef(null);

    useEffect(() => {
        const reduced = window.matchMedia('(prefers-reduced-motion: reduce)').matches;
        const ease = reduced ? 1 : smoothing;

        const setTarget = (x, y, active = true) => {
            target.current = { x, y, active };
        };

        const onMove = (e) => setTarget(e.clientX / window.innerWidth, e.clientY / window.innerHeight);
        const onLeave = () => setTarget(0.5, 0.5, false);
        const onTouch = (e) => {
            if (e.touches[0]) {
                setTarget(
                    e.touches[0].clientX / window.innerWidth,
                    e.touches[0].clientY / window.innerHeight,
                );
            }
        };

        window.addEventListener('mousemove', onMove);
        window.addEventListener('mouseleave', onLeave);
        window.addEventListener('touchmove', onTouch, { passive: true });

        const tick = () => {
            current.current = {
                x: current.current.x + (target.current.x - current.current.x) * ease,
                y: current.current.y + (target.current.y - current.current.y) * ease,
            };
            setMouse({
                x: current.current.x,
                y: current.current.y,
                active: target.current.active,
            });
            raf.current = requestAnimationFrame(tick);
        };
        raf.current = requestAnimationFrame(tick);

        return () => {
            window.removeEventListener('mousemove', onMove);
            window.removeEventListener('mouseleave', onLeave);
            window.removeEventListener('touchmove', onTouch);
            cancelAnimationFrame(raf.current);
        };
    }, [smoothing]);

    return mouse;
}
