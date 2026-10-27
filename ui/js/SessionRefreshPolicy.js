.pragma library

var RECONCILE_INTERVAL_MS = 600000;
var INCOMPLETE_RETRY_MS = 60000;
var RESUME_GAP_MS = 90000;

function cacheExpired(ageSeconds) {
    return ageSeconds === null || ageSeconds === undefined || Number(ageSeconds) * 1000 >= RECONCILE_INTERVAL_MS;
}

function refreshDelayMs(ageSeconds, refreshStatus) {
    if (refreshStatus === "incomplete")
        return INCOMPLETE_RETRY_MS;
    if (refreshStatus === "failed")
        return RECONCILE_INTERVAL_MS;
    if (ageSeconds === null || ageSeconds === undefined)
        return RECONCILE_INTERVAL_MS;
    if (Number(ageSeconds) * 1000 >= RECONCILE_INTERVAL_MS)
        return RECONCILE_INTERVAL_MS;
    return Math.max(1000, RECONCILE_INTERVAL_MS - Number(ageSeconds) * 1000);
}

function incompleteRetryDue(lastReconcileMs, nowMs) {
    return !lastReconcileMs || Number(nowMs) - Number(lastReconcileMs) >= INCOMPLETE_RETRY_MS;
}

function resumed(previousMs, nowMs) {
    return Number(previousMs) > 0 && Number(nowMs) - Number(previousMs) > RESUME_GAP_MS;
}
