import { useSmoothMouse } from '../hooks/useSmoothMouse';

const InteractiveBackground = () => {
    const { x, y, active } = useSmoothMouse(0.06);

    const px = `${x * 100}%`;
    const py = `${y * 100}%`;
    const orb1X = (x - 0.5) * 80;
    const orb1Y = (y - 0.5) * 60;
    const orb2X = (x - 0.5) * -100;
    const orb2Y = (y - 0.5) * -70;
    const orb3X = (x - 0.5) * 60;
    const orb3Y = (y - 0.5) * 90;

    return (
        <div
            className="interactive-bg"
            aria-hidden="true"
            style={{
                '--mouse-x': px,
                '--mouse-y': py,
                '--orb-1-x': `${orb1X}px`,
                '--orb-1-y': `${orb1Y}px`,
                '--orb-2-x': `${orb2X}px`,
                '--orb-2-y': `${orb2Y}px`,
                '--orb-3-x': `${orb3X}px`,
                '--orb-3-y': `${orb3Y}px`,
                '--spotlight-opacity': active ? 1 : 0.35,
            }}
        >
            <div className="aurora-base" />
            <div className="aurora-orb aurora-orb-1" />
            <div className="aurora-orb aurora-orb-2" />
            <div className="aurora-orb aurora-orb-3" />
            <div className="cursor-spotlight" />
            <div className="aurora-grid aurora-grid-interactive" />
            <div className="floating-particles" />
        </div>
    );
};

export default InteractiveBackground;
