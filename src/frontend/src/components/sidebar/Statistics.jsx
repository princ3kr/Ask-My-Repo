import React from 'react';

export default function Statistics({ stats }) {
    const data = [
        { label: 'Files', value: stats?.files || 0 },
        { label: 'Classes', value: stats?.classes || 0 },
        { label: 'Functions', value: stats?.functions || 0 },
        { label: 'Imports', value: stats?.imports || 0 },
        { label: 'Calls', value: stats?.calls || 0 },
        { label: 'Nodes', value: stats?.nodes || 0 },
    ];

    return (
        <div className="flex flex-col border-t border-surface-muted h-64 shrink-0">
            <div className="panel-header">Statistics</div>
            <div className="p-4 grid grid-cols-2 gap-3 overflow-y-auto">
                {data.map((item) => (
                    <div key={item.label} className="stat-card">
                        <span className="text-xs text-text-dim font-medium">{item.label}</span>
                        <span className="text-lg text-gray-200 mt-1 font-semibold">{item.value >= 1000 ? (item.value / 1000).toFixed(1) + 'K' : item.value}</span>
                    </div>
                ))}
            </div>
        </div>
    );
}
