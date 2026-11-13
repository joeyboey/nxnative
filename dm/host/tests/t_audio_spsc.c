// hle_audio.c lock-free queue stress test: three threads producing / playing / reclaiming 100 thousand blocks concurrently, the byte stream and reclaim order checked one by one by sequence number, no event lost.
#include "../hle_audio.c"
__thread _Atomic int dm_host_work;   // defined by the graphics host in a full build
#include <sched.h>
#include <time.h>

enum { NBUF = 100000, MAX_SIZE = 37 };

uint64_t now_ns(void) { struct timespec t; clock_gettime(CLOCK_MONOTONIC, &t); return (uint64_t)t.tv_sec * 1000000000ull + t.tv_nsec; }
static struct { const void *k; void *v; } om[16];
static pthread_mutex_t om_lock = PTHREAD_MUTEX_INITIALIZER;
void *objmap_get(const void *k) {
    pthread_mutex_lock(&om_lock);
    void *v = NULL;
    for (int i = 0; i < 16; i++) if (om[i].k == k) { v = om[i].v; break; }
    pthread_mutex_unlock(&om_lock);
    return v;
}
void objmap_put(const void *k, void *v) {
    pthread_mutex_lock(&om_lock);
    for (int i = 0; i < 16; i++) if (!om[i].k) { om[i].k = k; om[i].v = v; break; }
    pthread_mutex_unlock(&om_lock);
}
void *objmap_del(const void *k) {
    pthread_mutex_lock(&om_lock);
    void *v = NULL;
    for (int i = 0; i < 16; i++) if (om[i].k == k) { v = om[i].v; om[i].k = 0; break; }
    pthread_mutex_unlock(&om_lock);
    return v;
}

static _Atomic int g_sig, g_after_last;
static _Atomic int g_last_pull;
void hle_signal_system_event(void *g) {
    (void)g;
    atomic_fetch_add_explicit(&g_sig, 1, memory_order_seq_cst);
    if (atomic_load_explicit(&g_last_pull, memory_order_seq_cst)) atomic_store(&g_after_last, 1);
}
void hle_bind_system_event(void *g, int autoclear) { (void)g; (void)autoclear; }
module_t *module_of(const void *p) { (void)p; return NULL; }   // call sites in the log: no guest modules in the unit test
int dm_guest_as_contains(uintptr_t a) { (void)a; return 0; }   // hle_svc.c: no guest address space in the unit test, so the big-stack take-overs (Start / Stop / CloseAudioOut, hle_bigstack.c) run on the calling host stack
void dm_host_helper_qos(qos_class_t q) { (void)q; }   // hle_thread.c: the helper threads keep the default class in the unit test

typedef struct {
    void *out;
    aout *a;
    aout_buf *bufs;
    uint8_t (*data)[MAX_SIZE];
    uint8_t *scratch;
    uint32_t *sizes;
    uint64_t total_bytes;
    _Atomic int failed;
    _Atomic int released;
} test_ctx;

static uint32_t next_random(uint32_t *state) {
    *state = *state * 1664525u + 1013904223u;
    return *state;
}

static uint64_t available_bytes(aout *a) {
    uint32_t qh = atomic_load_explicit(&a->q_head, memory_order_relaxed);
    uint32_t qt = atomic_load_explicit(&a->q_tail, memory_order_acquire);
    if (qh == qt) return 0;
    uint64_t bytes = a->q[qh & (QMAX - 1)]->size - a->pos;
    for (uint32_t i = qh + 1; i != qt; i++) bytes += a->q[i & (QMAX - 1)]->size;
    return bytes;
}

static void *producer(void *p) {
    test_ctx *t = p;
    for (uint32_t i = 0; i < NBUF; i++) {
        t->bufs[i].ptr = t->data[i]; t->bufs[i].cap = MAX_SIZE; t->bufs[i].size = t->sizes[i];
        // Real buffer pool: buffers in flight (appended, not yet reclaimed) do not exceed QMAX, and more can be appended only after reclaiming
        while (i - (uint32_t)atomic_load_explicit(&t->released, memory_order_acquire) >= QMAX) sched_yield();
        while (!h_au_Append(t->out, &t->bufs[i])) sched_yield();
    }
    return NULL;
}

static void *consumer(void *p) {
    test_ctx *t = p;
    uint32_t state = 7;
    uint64_t want = 0, done = 0;
    while (done < t->total_bytes) {
        uint64_t avail = available_bytes(t->a);
        if (!avail) {
            if (atomic_load_explicit(&t->released, memory_order_acquire) == NBUF) break;
            sched_yield();
            continue;
        }
        uint64_t n = 1 + next_random(&state) % 512;
        if (n > avail) n = avail;
        if (done + n == t->total_bytes) atomic_store(&g_last_pull, 1);
        pull(t->a, t->scratch, n);
        for (uint64_t i = 0; i < n; i++) {
            if (t->scratch[i] != (uint8_t)(want++)) atomic_store(&t->failed, 1);
        }
        done += n;
        if (atomic_load(&t->released) == NBUF && !available_bytes(t->a)) break;
    }
    return NULL;
}

static void *release_reader(void *p) {
    test_ctx *t = p;
    for (int i = 0; i < NBUF; ) {
        aout_buf *b = h_au_Released(t->out);
        if (!b) { sched_yield(); continue; }
        if (b != &t->bufs[i]) atomic_store(&t->failed, 1);
        i++;
        atomic_store_explicit(&t->released, i, memory_order_release);
    }
    return NULL;
}

static int stop_pending(void) {
    char out[64], ev[64]; uint8_t prm[16], bytes[5][8] = {{0}}, played[13]; aout_buf buffers[5] = {0};
    h_au_InitParam(prm); if (h_au_Open(out, ev, NULL, prm)) return 1;
    aout *a = ao(out); atomic_store(&a->state, STATE_STARTED);   // deterministic callback, no backend thread
    atomic_store(&a->q_head, UINT32_MAX-2); atomic_store(&a->q_tail, UINT32_MAX-2);
    atomic_store(&a->rel_head, UINT32_MAX-1); atomic_store(&a->rel_tail, UINT32_MAX-1);
    for (int i=0; i<5; i++) { buffers[i].ptr=bytes[i]; buffers[i].size=buffers[i].cap=8; if (!h_au_Append(out,&buffers[i])) return 1; }
    pull(a,played,sizeof played);   // one released buffer followed by a partly played one
    a->rel_sem=dispatch_semaphore_create(0);
    h_au_Stop(out);
    int failed = h_au_BufCount(out)!=0 || a->pos!=0 || dispatch_semaphore_wait(a->rel_sem,dispatch_time(DISPATCH_TIME_NOW,100000000))!=0;
    for (int i=0; i<5; i++) failed |= h_au_Released(out)!=&buffers[i];
    h_au_Stop(out); failed |= h_au_Released(out)!=NULL;
    // Buffers queued while stopped belong to the next Start; a repeated Stop keeps them.
    h_au_Append(out,&buffers[0]); h_au_Stop(out); failed |= h_au_BufCount(out)!=1;
    atomic_store(&a->state,STATE_STARTED); h_au_Stop(out); failed |= h_au_Released(out)!=&buffers[0] || h_au_Released(out)!=NULL;
    h_au_Close(out);
    printf("Stop releases partial/pending buffers in order, signals and preserves pre-start buffers: %s\n",failed?"FAIL":"PASS");
    atomic_store(&g_sig,0); return failed;
}

typedef struct {
    void *out;
    _Atomic int entered;
    _Atomic int stop;
    _Atomic int closed;
} pcmout_wait_ctx;

static void *pcmout_wait_worker(void *p) {
    pcmout_wait_ctx *c = p;
    atomic_store(&c->entered, 1);
    while (!atomic_load(&c->stop)) dm_pcmout_wait(c->out, 5000);
    dm_pcmout_close(c->out);
    atomic_store(&c->closed, 1);
    return NULL;
}

static int pcmout_teardown_while_waiting(void) {
    pcmout_wait_ctx c = { .out = dm_pcmout_open(2) };
    if (!c.out) return 1;
    pthread_t th;
    if (pthread_create(&th, NULL, pcmout_wait_worker, &c)) return 1;
    while (!atomic_load(&c.entered)) sched_yield();
    usleep(20000);
    uint64_t t0 = now_ns();
    atomic_store(&c.stop, 1);
    dm_pcmout_wake(c.out);
    pthread_join(th, NULL);
    uint64_t elapsed_ms = (now_ns() - t0) / 1000000ull;
    dm_audio_stop_all();

    // Also verify idempotency if dm_audio_stop_all ran first before a concurrent dm_pcmout_close.
    void *out2 = dm_pcmout_open(2);
    if (!out2) return 1;
    dm_audio_stop_all();
    dm_pcmout_close(out2);

    // Concurrent AudioOut append/close, OutputStream write/purge/close, dm_pcmout_close, and dm_audio_stop_all.
    char dev[0x100];
    h_os_DefaultDev(dev);
    int32_t fmt[3] = {OS_FLOAT, 2, 48000};
    static float os_pcm[2 * 120];
    static int16_t au_pcm[120 * 2];
    uint8_t prm[16];
    h_au_InitParam(prm);
    for (int r = 0; r < 50; r++) {
        char au[64] = {0}, ev[64] = {0}, stream[0x350] = {0};
        aout_buf gb = {.ptr = au_pcm, .cap = sizeof au_pcm, .size = sizeof au_pcm};
        if (h_au_Open(au, ev, NULL, prm)) return 1;
        h_au_Start(au);
        (void)h_au_Append(au, &gb);
        if (h_os_Open(stream, NULL, NULL, 0, dev, fmt, 1200, 600, 0)) return 1;
        (void)h_os_Write(stream, os_pcm, 120);
        (void)h_os_Start(stream);
        void *po = dm_pcmout_open(2);
        if (!po) return 1;
        dm_pcmout_start(po);

        dm_audio_stop_all();
        // Late guest calls after stop_all must degrade safely without die() or UAF.
        (void)h_au_Append(au, &gb);
        (void)h_au_Released(au);
        (void)h_au_BufCount(au);
        h_au_Close(au);
        h_os_Purge(stream);
        h_os_Close(stream);
        dm_pcmout_close(po);
    }

    // Reopen OutputStream after repeated stop_all cycles without touching internal globals.
    char reopen_stream[0x350] = {0};
    if (h_os_Open(reopen_stream, NULL, NULL, 0, dev, fmt, 480, 240, 0)) return 1;
    if (h_os_Write(reopen_stream, os_pcm, 120) != 120 || h_os_Start(reopen_stream)) return 1;
    uint64_t wait0 = now_ns();
    while (h_os_Free(reopen_stream) != 480 && now_ns() - wait0 < 2000000000ull) usleep(1000);
    int reopen_ok = h_os_Free(reopen_stream) == 480 && g_suspended == 0 && g_wd_stop == 0;
    h_os_Close(reopen_stream);
    atomic_store(&g_sig, 0);

    int failed = !atomic_load(&c.closed) || elapsed_ms >= 500 || !reopen_ok;
    printf("pcmout wake + teardown while waiting (%llu ms) + stop_all reopen: %s\n", (unsigned long long)elapsed_ms, failed ? "FAIL" : "PASS");
    return failed;
}
int main(void) {
    if (stop_pending()) return 1;
    if (pcmout_teardown_while_waiting()) return 1;
    char out[64], ev[64]; uint8_t prm[16];
    test_ctx t = {0};
    t.bufs = calloc(NBUF, sizeof *t.bufs); t.data = calloc(NBUF, sizeof *t.data); t.sizes = calloc(NBUF, sizeof *t.sizes); t.scratch = malloc(512);
    if (!t.bufs || !t.data || !t.sizes || !t.scratch) return 2;
    h_au_InitParam(prm);
    uint32_t state = 1; uint64_t cursor = 0;
    for (uint32_t i = 0; i < NBUF; i++) {
        t.sizes[i] = 1 + next_random(&state) % MAX_SIZE;
        for (uint32_t j = 0; j < t.sizes[i]; j++) t.data[i][j] = (uint8_t)(cursor++);
        t.total_bytes += t.sizes[i];
    }
    if (h_au_Open(out, ev, NULL, prm)) return 3;
    t.out = out; t.a = ao(out);
    atomic_store(&t.a->state, STATE_STARTED);
    pthread_t p, c, r;
    pthread_create(&p, NULL, producer, &t);
    pthread_create(&c, NULL, consumer, &t);
    pthread_create(&r, NULL, release_reader, &t);
    pthread_join(p, NULL); pthread_join(c, NULL); pthread_join(r, NULL);
    atomic_store(&t.a->state, STATE_STOPPED);
    h_au_Close(out);
    int sig = atomic_load(&g_sig), after = atomic_load(&g_after_last), released = atomic_load(&t.released), failed = atomic_load(&t.failed);
    printf("SPSC: appended/released %d/%d, events %d, signaled after last release=%d, failed=%d\n", NBUF, released, sig, after, failed);
    free(t.scratch); free(t.sizes); free(t.data); free(t.bufs);
    return released != NBUF || failed || sig < 1 || !after;
}

void dm_audio_device_changed(void) {}   // renderer notification delivery is checked by t_audren_work
