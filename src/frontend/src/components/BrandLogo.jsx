import { useRef } from 'react';
import { useSmoothMouse } from '../hooks/useSmoothMouse';
import logo from '../assets/Ask-My-repo.png';

export const PRODUCT_NAME = 'Ask My repo';

/**
 * @param {'hero' | 'header' | 'compact'} variant
 */
const BrandLogo = ({ variant = 'header', className = '' }) => {
    const wrapRef = useRef(null);
    const { x, y, active } = useSmoothMouse(0.06);
    const isHero = variant === 'hero';
    const isInteractive = variant !== 'compact';

    const handleMove = (e) => {
        if (!isInteractive || !wrapRef.current) return;
        const rect = wrapRef.current.getBoundingClientRect();
        const px = ((e.clientX - rect.left) / rect.width - 0.5) * 2;
        const py = ((e.clientY - rect.top) / rect.height - 0.5) * 2;
        wrapRef.current.style.setProperty('--logo-tilt-x', `${py * -6}deg`);
        wrapRef.current.style.setProperty('--logo-tilt-y', `${px * 6}deg`);
        wrapRef.current.style.setProperty('--logo-shine-x', `${((e.clientX - rect.left) / rect.width) * 100}%`);
    };

    const handleLeave = () => {
        if (!wrapRef.current) return;
        wrapRef.current.style.setProperty('--logo-tilt-x', '0deg');
        wrapRef.current.style.setProperty('--logo-tilt-y', '0deg');
        wrapRef.current.style.setProperty('--logo-shine-x', '50%');
    };

    const parallaxX = isHero ? (x - 0.5) * 12 : 0;
    const parallaxY = isHero ? (y - 0.5) * 8 : 0;

    return (
        <div
            ref={wrapRef}
            className={`brand-logo-wrap brand-logo-${variant} ${active && isInteractive ? 'brand-logo-active' : ''} ${className}`}
            onMouseMove={handleMove}
            onMouseLeave={handleLeave}
            style={isHero ? {
                '--parallax-x': `${parallaxX}px`,
                '--parallax-y': `${parallaxY}px`,
            } : undefined}
        >
            <div className="brand-logo-glow" aria-hidden="true" />
            <div className="brand-logo-shine" aria-hidden="true" />
            <img
                src={logo}
                alt={PRODUCT_NAME}
                className="brand-logo-img"
                draggable={false}
            />
        </div>
    );
};

export default BrandLogo;
