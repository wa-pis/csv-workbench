# Design

Qt Cocoa `populateTableRow` and `accessibilityChildren` materialize synthetic cells for all columns. The Python binding does not expose QAccessibleTableInterface, so a Python implementation cannot safely override its eager table enumeration. A scoped QAccessibleWidget factory uses an AXGroup of visible cells for application CSV views on macOS instead. Each cell announces its source row, column name, value, selected/focused state; press/focus actions target the real model index. Standard line editors and scrollbars remain native accessible objects. Keyboard navigation scrolls further cells into this accessible viewport. This deliberately changes macOS table-role navigation to group/cell navigation; it does not hide the table or disable accessibility.

Visible section lookup follows header viewport geometry, including moved/hidden columns. Root caches only visible cell widgets (up to 512 per view); model reset invalidates persistent indices. Pinned overlay is an accessible child and uses the same selection model. No selectedIndexes() expansion is permitted. Normal Qt interfaces remain on Windows/Linux and in non-CSV widgets.

Primary references: https://doc.qt.io/qt-6/accessible-qwidget.html and https://github.com/qt/qtbase/blob/6.12/src/plugins/platforms/cocoa/qcocoaaccessibilityelement.mm . Native regressions must traverse Cocoa children, not merely query Python wrappers. The original ownership regression remains exercised on a standard Qt table.

Accessible cell focus follows the real keyboard focus of the main view, including pinned cells whose overlay has NoFocus. Native line editors retain focus priority; moving focus outside the table clears the accessible cell focus.
