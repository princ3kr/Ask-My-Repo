// Split out of CustomNodes.jsx: react-refresh/only-export-components requires a
// module to export components only, and this is a plain map of component name
// -> component.
import { CustomNode, GroupNode } from './CustomNodes';

export const nodeTypes = {
    customNode: CustomNode,
    groupNode: GroupNode,
};
