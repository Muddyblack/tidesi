.pragma library

// The order providers are listed in. The backend and ProviderRegistry.js fix a
// canonical order; the user can drag providers into their own (Settings →
// Providers), kept as a list of ids in the `providerOrder` setting. Nothing
// here names a provider, so any list of `{ id }` objects can be arranged.

function indexOfId(ids, id) {
    return Array.isArray(ids) ? ids.indexOf(id) : -1;
}

// `items` (objects with an `id`) arranged by the user's `order`. An item the
// user never placed, such as a provider added in a later release, follows the
// placed ones in `canonical` order, then in its incoming order. Returns a new
// array; `items` is left alone.
function sort(items, order, canonical) {
    var placed = Array.isArray(order) ? order.length : 0;
    var canon = (canonical || []).map(function (entry) {
        return entry.id;
    });
    function rank(item) {
        var chosen = indexOfId(order, item.id);
        if (chosen !== -1)
            return chosen;
        var known = canon.indexOf(item.id);
        return placed + (known === -1 ? canon.length : known);
    }
    return (items || []).map(function (item, position) {
        return {
            item: item,
            rank: rank(item),
            position: position
        };
    }).sort(function (a, b) {
        return a.rank - b.rank || a.position - b.position;
    }).map(function (entry) {
        return entry.item;
    });
}

// The saved order after the user arranges `ids` (the providers on screen, in
// their new order). Ids that are not on screen, such as providers switched off
// for now, keep their place after them rather than being forgotten.
function merge(order, ids) {
    var arranged = (ids || []).slice();
    var rest = (Array.isArray(order) ? order : []).filter(function (id) {
        return arranged.indexOf(id) === -1;
    });
    return arranged.concat(rest);
}

// `ids` with the entry at `from` moved to `to`; out-of-range moves change nothing.
function move(ids, from, to) {
    var next = (ids || []).slice();
    if (from === to || from < 0 || to < 0 || from >= next.length || to >= next.length)
        return next;
    next.splice(to, 0, next.splice(from, 1)[0]);
    return next;
}
