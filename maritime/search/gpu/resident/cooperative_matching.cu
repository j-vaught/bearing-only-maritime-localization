// One cooperative warp per candidate. No padded-column scan or host solver.
// Finite input edges must be nonnegative; positive infinity forbids an edge.
extern "C" __global__ void cooperative_marker_hungarian(
    const double* costs, const long long* counts, int B, int R, int G,
    double* u_all, double* v_all, int* p_all, int* way_all,
    double* minv_all, unsigned char* used_all,
    int* columns, int* matched_counts, double* residuals, int* errors
) {
    const int b = blockIdx.x;
    const int lane = threadIdx.x;
    if (b >= B || lane >= 32) return;
    const unsigned int mask = 0xffffffffu;
    __shared__ int j0_shared, row_shared, next_shared, done_shared;
    __shared__ double delta_shared;
    const long long raw_count = counts[b];
    if (lane == 0) { errors[b] = 0; matched_counts[b] = 0; residuals[b] = 0.; }
    for (int r = lane; r < R; r += 32) columns[(long long)b * R + r] = -1;
    __syncwarp(mask);
    if (raw_count < 0 || raw_count > G) {
        if (lane == 0) errors[b] = 1;
        return;
    }
    const int ng = (int)raw_count;
    if (R == 0 || ng == 0) return;
    const int M = ng + R;
    const int stride = G + R + 1;
    double* u = u_all + (long long)b * (R + 1);
    double* v = v_all + (long long)b * stride;
    int* p = p_all + (long long)b * stride;
    int* way = way_all + (long long)b * stride;
    double* minv = minv_all + (long long)b * stride;
    unsigned char* used = used_all + (long long)b * stride;
    double maximum = 0.;
    bool invalid = false;
    for (long long k = lane; k < (long long)R * ng; k += 32) {
        int r = (int)(k / ng), c = (int)(k % ng);
        double value = costs[((long long)b * R + r) * G + c];
        if (isnan(value) || value < 0.) invalid = true;
        if (isfinite(value)) maximum = fmax(maximum, value);
    }
    if (__any_sync(mask, invalid)) {
        if (lane == 0) errors[b] = 2;
        return;
    }
    for (int step = 16; step > 0; step /= 2)
        maximum = fmax(maximum, __shfl_down_sync(mask, maximum, step));
    maximum = __shfl_sync(mask, maximum, 0);
    const double penalty = (R + 1.) * fmax(1., maximum);
    const double forbidden = (R + 1.) * penalty;
    if (!isfinite(penalty) || !isfinite(forbidden)) {
        if (lane == 0) errors[b] = 3;
        return;
    }
    for (int k = lane; k <= R; k += 32) u[k] = 0.;
    for (int k = lane; k <= M; k += 32) { v[k] = 0.; p[k] = 0; way[k] = 0; }
    __syncwarp(mask);
    for (int i = 1; i <= R; ++i) {
        if (lane == 0) { p[0] = i; j0_shared = 0; }
        for (int j = lane; j <= M; j += 32) {
            minv[j] = 1.7976931348623157e308;
            used[j] = 0;
        }
        __syncwarp(mask);
        while (true) {
            if (lane == 0) {
                used[j0_shared] = 1;
                row_shared = p[j0_shared];
            }
            __syncwarp(mask);
            const int r = row_shared - 1;
            double best = 1.7976931348623157e308;
            int best_column = 2147483647;
            for (int j = lane + 1; j <= M; j += 32) {
                if (used[j]) continue;
                const int c = j - 1;
                double value;
                if (c < ng) {
                    double edge = costs[((long long)b * R + r) * G + c];
                    value = isfinite(edge) ? edge : forbidden;
                } else {
                    value = c == ng + r ? penalty : forbidden;
                }
                const double current = value - u[row_shared] - v[j];
                if (current < minv[j]) { minv[j] = current; way[j] = j0_shared; }
                if (minv[j] < best || (minv[j] == best && j < best_column)) {
                    best = minv[j]; best_column = j;
                }
            }
            for (int step = 16; step > 0; step /= 2) {
                const double other = __shfl_down_sync(mask, best, step);
                const int other_column = __shfl_down_sync(mask, best_column, step);
                if (other < best || (other == best && other_column < best_column)) {
                    best = other; best_column = other_column;
                }
            }
            if (lane == 0) {
                if (!isfinite(best) || best_column == 2147483647) errors[b] = 4;
                delta_shared = best;
                next_shared = best_column;
            }
            __syncwarp(mask);
            if (errors[b]) return;
            for (int j = lane; j <= M; j += 32) {
                if (used[j]) {
                    u[p[j]] += delta_shared;
                    v[j] -= delta_shared;
                } else minv[j] -= delta_shared;
            }
            __syncwarp(mask);
            if (lane == 0) {
                j0_shared = next_shared;
                done_shared = p[j0_shared] == 0;
            }
            __syncwarp(mask);
            if (done_shared) break;
        }
        if (lane == 0) {
            int j0 = j0_shared;
            do {
                int next = way[j0];
                p[j0] = p[next];
                j0 = next;
            } while (j0 != 0);
        }
        __syncwarp(mask);
    }
    for (int j = lane + 1; j <= M; j += 32) {
        if (p[j] <= 0) continue;
        const int r = p[j] - 1, c = j - 1;
        columns[(long long)b * R + r] = c < ng &&
            isfinite(costs[((long long)b * R + r) * G + c]) ? c : -1;
    }
    __syncwarp(mask);
    int n = 0;
    double sum = 0.;
    for (int r = lane; r < R; r += 32) {
        const int c = columns[(long long)b * R + r];
        if (c >= 0) { ++n; sum += costs[((long long)b * R + r) * G + c]; }
    }
    for (int step = 16; step > 0; step /= 2) {
        n += __shfl_down_sync(mask, n, step);
        sum += __shfl_down_sync(mask, sum, step);
    }
    if (lane == 0) {
        matched_counts[b] = n;
        residuals[b] = sum;
        if (!isfinite(sum)) errors[b] = 5;
    }
}
