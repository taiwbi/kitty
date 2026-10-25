/*
 * Optional GTK/libadwaita entry points for the GNOME window backend.
 *
 * The terminal core remains independent of GTK when the optional development
 * packages were not present at build time.
 */

#include "state.h"

#ifdef KITTY_WITH_GTK
#include <adwaita.h>
#include <gtk/gtk.h>
#include "glfw-wrapper.h"
#include <stdatomic.h>
#ifdef KITTY_WITH_GTK_WAYLAND_BLUR
#include <gdk/wayland/gdkwayland.h>
#include <wayland-client.h>
#include <stdlib.h>
#include <string.h>
#include "../glfw/wayland-ext-background-effect-v1-client-protocol.h"
#endif
static bool gtk_backend_initialized = false;
static bool gtk_backend_active = false;
static bool gtk_backend_enabled = false;
static GMainLoop *gtk_main_loop = NULL;
static guint gtk_glfw_poll_source_id = 0;
static tick_callback_fun gtk_tick_callback = NULL;
static void *gtk_tick_callback_data = NULL;
static atomic_bool gtk_tick_pending = false;

typedef struct GTKTimer {
    id_type id;
    guint source_id;
    bool repeats, enabled, removed;
    timer_callback_fun callback, free_callback;
    void *callback_data;
    struct GTKTimer *next;
} GTKTimer;

static GTKTimer *gtk_timers = NULL;
static id_type next_gtk_timer_id = 0;

#ifdef KITTY_WITH_GTK_WAYLAND_BLUR
typedef struct GTKWaylandBlurSurface {
    id_type window_id;
    GdkSurface *gdk_surface;
    struct wl_surface *wl_surface;
    struct ext_background_effect_surface_v1 *effect;
    int blur_radius;
    int region_width, region_height;
    bool region_set;
    struct GTKWaylandBlurSurface *next;
} GTKWaylandBlurSurface;

static struct wl_display *gtk_blur_display = NULL;
static struct wl_compositor *gtk_blur_compositor = NULL;
static struct wl_registry *gtk_blur_registry = NULL;
static struct ext_background_effect_manager_v1 *gtk_blur_manager = NULL;
static uint32_t gtk_blur_capabilities = 0;
static uint32_t gtk_blur_manager_name = 0;
static GTKWaylandBlurSurface *gtk_blur_surfaces = NULL;

static void gtk_wayland_blur_update(GTKWaylandBlurSurface *entry);

static void
gtk_wayland_blur_capabilities(void UNUSED *data, struct ext_background_effect_manager_v1 UNUSED *manager, uint32_t flags) {
    gtk_blur_capabilities = flags;
    for (GTKWaylandBlurSurface *entry = gtk_blur_surfaces; entry; entry = entry->next) gtk_wayland_blur_update(entry);
    if (gtk_blur_display) wl_display_flush(gtk_blur_display);
}

static const struct ext_background_effect_manager_v1_listener gtk_wayland_blur_listener = {
    gtk_wayland_blur_capabilities,
};

static void
gtk_wayland_blur_global(void UNUSED *data, struct wl_registry *registry, uint32_t name, const char *interface, uint32_t version) {
    if (!gtk_blur_manager && strcmp(interface, "ext_background_effect_manager_v1") == 0) {
        gtk_blur_manager_name = name;
        gtk_blur_manager = wl_registry_bind(registry, name, &ext_background_effect_manager_v1_interface, MIN(version, 1));
        if (gtk_blur_manager) {
            ext_background_effect_manager_v1_add_listener(gtk_blur_manager, &gtk_wayland_blur_listener, NULL);
            wl_display_flush(gtk_blur_display);
        }
    }
}

static void
gtk_wayland_blur_global_remove(void UNUSED *data, struct wl_registry UNUSED *registry, uint32_t name) {
    if (name != gtk_blur_manager_name) return;
    gtk_blur_manager_name = 0;
    gtk_blur_capabilities = 0;
    if (gtk_blur_manager) {
        ext_background_effect_manager_v1_destroy(gtk_blur_manager);
        gtk_blur_manager = NULL;
    }
    for (GTKWaylandBlurSurface *entry = gtk_blur_surfaces; entry; entry = entry->next) {
        if (entry->effect) {
            ext_background_effect_surface_v1_destroy(entry->effect);
            entry->effect = NULL;
        }
        entry->region_set = false;
    }
}

static const struct wl_registry_listener gtk_wayland_blur_registry_listener = {
    gtk_wayland_blur_global,
    gtk_wayland_blur_global_remove,
};

static void
gtk_wayland_blur_initialize(GdkDisplay *display) {
    if (gtk_blur_registry || !display || !GDK_IS_WAYLAND_DISPLAY(display)) return;
    gtk_blur_display = gdk_wayland_display_get_wl_display(display);
    gtk_blur_compositor = gdk_wayland_display_get_wl_compositor(display);
    if (!gtk_blur_display || !gtk_blur_compositor) return;
    gtk_blur_registry = wl_display_get_registry(gtk_blur_display);
    if (gtk_blur_registry) {
        wl_registry_add_listener(gtk_blur_registry, &gtk_wayland_blur_registry_listener, NULL);
        wl_display_flush(gtk_blur_display);
    }
}

static void
gtk_wayland_blur_update(GTKWaylandBlurSurface *entry) {
    if (!entry->effect && gtk_blur_manager && entry->blur_radius > 0 &&
        (gtk_blur_capabilities & EXT_BACKGROUND_EFFECT_MANAGER_V1_CAPABILITY_BLUR)) {
        entry->effect = ext_background_effect_manager_v1_get_background_effect(gtk_blur_manager, entry->wl_surface);
    }
    if (!entry->effect) return;
    if (entry->blur_radius <= 0 || !gtk_blur_manager ||
        !(gtk_blur_capabilities & EXT_BACKGROUND_EFFECT_MANAGER_V1_CAPABILITY_BLUR)) {
        if (entry->region_set) {
            ext_background_effect_surface_v1_set_blur_region(entry->effect, NULL);
            entry->region_set = false;
            gdk_surface_queue_render(entry->gdk_surface);
        }
        return;
    }
    const int width = gdk_surface_get_width(entry->gdk_surface), height = gdk_surface_get_height(entry->gdk_surface);
    if (entry->region_set && entry->region_width == width && entry->region_height == height) return;
    struct wl_region *region = width > 0 && height > 0 ? wl_compositor_create_region(gtk_blur_compositor) : NULL;
    if (region) {
        wl_region_add(region, 0, 0, width, height);
        ext_background_effect_surface_v1_set_blur_region(entry->effect, region);
        wl_region_destroy(region);
        entry->region_width = width;
        entry->region_height = height;
        entry->region_set = true;
        gdk_surface_queue_render(entry->gdk_surface);
    }
}

static void *
gtk_pointer_from_capsule(PyObject *capsule) {
    if (!PyCapsule_CheckExact(capsule)) {
        PyErr_SetString(PyExc_TypeError, "expected a PyGObject native pointer capsule");
        return NULL;
    }
    const char *name = PyCapsule_GetName(capsule);
    if (!name && PyErr_Occurred()) return NULL;
    return PyCapsule_GetPointer(capsule, name);
}

static GTKWaylandBlurSurface *
gtk_wayland_blur_surface_for_id(id_type window_id) {
    for (GTKWaylandBlurSurface *entry = gtk_blur_surfaces; entry; entry = entry->next) {
        if (entry->window_id == window_id) return entry;
    }
    return NULL;
}

static void
gtk_wayland_blur_remove(id_type window_id) {
    GTKWaylandBlurSurface **link = &gtk_blur_surfaces;
    while (*link) {
        GTKWaylandBlurSurface *entry = *link;
        if (entry->window_id != window_id) {
            link = &entry->next;
            continue;
        }
        if (entry->effect) ext_background_effect_surface_v1_destroy(entry->effect);
        g_object_unref(entry->gdk_surface);
        *link = entry->next;
        free(entry);
        if (gtk_blur_display) wl_display_flush(gtk_blur_display);
        return;
    }
}
#endif
#endif

PyObject *
gtk_backend_available(PyObject UNUSED *self, PyObject UNUSED *args) {
#ifdef KITTY_WITH_GTK
    Py_RETURN_TRUE;
#else
    Py_RETURN_FALSE;
#endif
}

PyObject *
gtk_backend_initialize(PyObject UNUSED *self, PyObject UNUSED *args) {
#ifdef KITTY_WITH_GTK
    if (!gtk_backend_initialized) {
        gtk_init();
        adw_init();
        gtk_backend_initialized = true;
    }
    gtk_backend_active = true;
    Py_RETURN_NONE;
#else
    PyErr_SetString(PyExc_RuntimeError, "kitty was built without GTK 4 and libadwaita development libraries");
    return NULL;
#endif
}

PyObject *
gtk_backend_set_enabled(PyObject UNUSED *self, PyObject UNUSED *args) {
#ifdef KITTY_WITH_GTK
    int enabled = 0;
    if (!PyArg_ParseTuple(args, "p", &enabled)) return NULL;
    if (enabled && !gtk_backend_initialized) {
        PyErr_SetString(PyExc_RuntimeError, "GTK backend has not been initialized");
        return NULL;
    }
    gtk_backend_enabled = enabled != 0;
    gtk_backend_active = gtk_backend_enabled;
    Py_RETURN_NONE;
#else
    PyErr_SetString(PyExc_RuntimeError, "kitty was built without GTK 4 and libadwaita development libraries");
    return NULL;
#endif
}

PyObject *
gtk_backend_activate(PyObject UNUSED *self, PyObject UNUSED *args) {
#ifdef KITTY_WITH_GTK
    if (!gtk_backend_initialized) {
        PyErr_SetString(PyExc_RuntimeError, "GTK backend has not been initialized");
        return NULL;
    }
    gtk_backend_active = true;
    Py_RETURN_NONE;
#else
    PyErr_SetString(PyExc_RuntimeError, "kitty was built without GTK 4 and libadwaita development libraries");
    return NULL;
#endif
}

PyObject *
gtk_backend_is_active(PyObject UNUSED *self, PyObject UNUSED *args) {
#ifdef KITTY_WITH_GTK
    return PyBool_FromLong(gtk_backend_active);
#else
    Py_RETURN_FALSE;
#endif
}

PyObject *
gtk_backend_register_window(PyObject UNUSED *self, PyObject UNUSED *args) {
#ifdef KITTY_WITH_GTK
    unsigned long long window_id = 0;
    PyObject *window = NULL;
    if (!PyArg_ParseTuple(args, "KO", &window_id, &window)) return NULL;
    OSWindow *osw = os_window_for_id((id_type)window_id);
    if (!osw) {
        PyErr_SetString(PyExc_ValueError, "No OSWindow with the specified id found");
        return NULL;
    }
    if (osw->gtk_window != window) {
        Py_XINCREF(window);
        Py_XDECREF(osw->gtk_window);
        osw->gtk_window = window;
    }
    PyObject *mapped = PyObject_CallMethod(window, "is_mapped", NULL);
    if (mapped) {
        int is_mapped = PyObject_IsTrue(mapped);
        Py_DECREF(mapped);
        if (is_mapped < 0) return NULL;
        osw->gtk_window_mapped = is_mapped != 0;
    } else return NULL;
    PyObject *bound = PyObject_CallMethod(window, "bind", "K", window_id);
    if (!bound) return NULL;
    Py_DECREF(bound);
    gtk_backend_active = true;
    Py_RETURN_NONE;
#else
    PyErr_SetString(PyExc_RuntimeError, "kitty was built without GTK 4 and libadwaita development libraries");
    return NULL;
#endif
}

PyObject *
gtk_backend_set_window_blur(PyObject UNUSED *self, PyObject *args) {
    unsigned long long window_id = 0;
    PyObject *display_capsule = NULL, *surface_capsule = NULL;
    int blur_radius = 0;
    if (!PyArg_ParseTuple(args, "KOOi", &window_id, &display_capsule, &surface_capsule, &blur_radius)) return NULL;
#ifdef KITTY_WITH_GTK_WAYLAND_BLUR
    GdkDisplay *display = gtk_pointer_from_capsule(display_capsule);
    if (!display) return NULL;
    GdkSurface *surface = gtk_pointer_from_capsule(surface_capsule);
    if (!surface) return NULL;
    if (!GDK_IS_WAYLAND_DISPLAY(display) || !GDK_IS_WAYLAND_SURFACE(surface) || gdk_surface_get_display(surface) != display) Py_RETURN_NONE;
    struct wl_surface *wl_surface = gdk_wayland_surface_get_wl_surface(surface);
    if (!wl_surface) Py_RETURN_NONE;
    gtk_wayland_blur_initialize(display);
    if (!gtk_blur_display || gtk_blur_display != gdk_wayland_display_get_wl_display(display)) Py_RETURN_NONE;
    GTKWaylandBlurSurface *entry = gtk_wayland_blur_surface_for_id((id_type)window_id);
    if (!entry && blur_radius <= 0) Py_RETURN_NONE;
    if (!entry) {
        entry = calloc(1, sizeof(GTKWaylandBlurSurface));
        if (!entry) return PyErr_NoMemory();
        entry->window_id = (id_type)window_id;
        entry->next = gtk_blur_surfaces;
        gtk_blur_surfaces = entry;
    } else if (entry->gdk_surface != surface) {
        if (entry->effect) ext_background_effect_surface_v1_destroy(entry->effect);
        entry->effect = NULL;
        entry->region_set = false;
        g_clear_object(&entry->gdk_surface);
    }
    if (entry->gdk_surface != surface) {
        entry->gdk_surface = g_object_ref(surface);
        entry->wl_surface = wl_surface;
    }
    entry->blur_radius = blur_radius;
    gtk_wayland_blur_update(entry);
    wl_display_flush(gtk_blur_display);
#else
    (void)window_id;
    (void)display_capsule;
    (void)surface_capsule;
    (void)blur_radius;
#endif
    Py_RETURN_NONE;
}

PyObject *
gtk_backend_clear_window_blur(PyObject UNUSED *self, PyObject *args) {
    unsigned long long window_id = 0;
    if (!PyArg_ParseTuple(args, "K", &window_id)) return NULL;
#ifdef KITTY_WITH_GTK_WAYLAND_BLUR
    gtk_wayland_blur_remove((id_type)window_id);
#else
    (void)window_id;
#endif
    Py_RETURN_NONE;
}

PyObject *
gtk_backend_main_context_iteration(PyObject UNUSED *self, PyObject UNUSED *args) {
#ifdef KITTY_WITH_GTK
    int may_block = 0;
    if (!gtk_backend_initialized) {
        PyErr_SetString(PyExc_RuntimeError, "GTK backend has not been initialized");
        return NULL;
    }
    if (!PyArg_ParseTuple(args, "|p", &may_block)) return NULL;
    return PyBool_FromLong(g_main_context_iteration(NULL, may_block));
#else
    PyErr_SetString(PyExc_RuntimeError, "kitty was built without GTK 4 and libadwaita development libraries");
    return NULL;
#endif
}

#ifdef KITTY_WITH_GTK
static gboolean
gtk_dispatch_tick(gpointer UNUSED data) {
    atomic_store(&gtk_tick_pending, false);
    if (gtk_main_loop && gtk_tick_callback) gtk_tick_callback(gtk_tick_callback_data);
    return G_SOURCE_REMOVE;
}

static gboolean
gtk_poll_glfw_events(gpointer UNUSED data) {
    glfwPollEvents();
    return G_SOURCE_CONTINUE;
}

static void
gtk_schedule_tick(void) {
    if (atomic_exchange(&gtk_tick_pending, true)) return;
    GSource *source = g_idle_source_new();
    g_source_set_callback(source, gtk_dispatch_tick, NULL, NULL);
    g_source_attach(source, NULL);
    g_source_unref(source);
}

static guint
gtk_timer_interval_ms(monotonic_t interval) {
    const double ms = monotonic_t_to_s_double(interval) * 1000.0;
    if (ms >= G_MAXUINT) return G_MAXUINT;
    return MAX(1, (guint)ms);
}

static gboolean
gtk_timer_callback(gpointer data) {
    GTKTimer *timer = data;
    const guint fired_source = timer->source_id;
    if (timer->removed || !timer->enabled) return G_SOURCE_REMOVE;
    timer->callback(timer->id, timer->callback_data);
    if (timer->removed || !timer->enabled || timer->source_id != fired_source) return G_SOURCE_REMOVE;
    if (!timer->repeats) {
        timer->source_id = 0;
        timer->enabled = false;
        if (timer->free_callback) {
            timer->free_callback(timer->id, timer->callback_data);
            timer->free_callback = NULL;
        }
        return G_SOURCE_REMOVE;
    }
    return G_SOURCE_CONTINUE;
}

bool
is_gtk_backend_active(void) {
    return gtk_backend_active;
}

bool
is_gtk_backend_enabled(void) {
    return gtk_backend_enabled;
}

void
gtk_backend_wakeup(void) {
    if (gtk_backend_active) gtk_schedule_tick();
}

id_type
gtk_backend_add_main_loop_timer(monotonic_t interval, bool repeats, timer_callback_fun callback, void *callback_data, timer_callback_fun free_callback) {
    GTKTimer *timer = calloc(1, sizeof(GTKTimer));
    if (!timer) fatal("Out of memory allocating a GTK main loop timer");
    timer->id = ++next_gtk_timer_id;
    timer->repeats = repeats;
    timer->callback = callback;
    timer->callback_data = callback_data;
    timer->free_callback = free_callback;
    timer->next = gtk_timers;
    gtk_timers = timer;
    gtk_backend_update_main_loop_timer(timer->id, interval, true);
    return timer->id;
}

void
gtk_backend_update_main_loop_timer(id_type timer_id, monotonic_t interval, bool enabled) {
    for (GTKTimer *timer = gtk_timers; timer; timer = timer->next) {
        if (timer->id != timer_id || timer->removed) continue;
        if (timer->source_id) {
            g_source_remove(timer->source_id);
            timer->source_id = 0;
        }
        timer->enabled = enabled;
        if (enabled) timer->source_id = g_timeout_add(gtk_timer_interval_ms(interval), gtk_timer_callback, timer);
        return;
    }
}

void
gtk_backend_remove_main_loop_timer(id_type timer_id) {
    for (GTKTimer *timer = gtk_timers; timer; timer = timer->next) {
        if (timer->id != timer_id || timer->removed) continue;
        timer->removed = true;
        timer->enabled = false;
        if (timer->source_id) {
            g_source_remove(timer->source_id);
            timer->source_id = 0;
        }
        if (timer->free_callback) {
            timer->free_callback(timer->id, timer->callback_data);
            timer->free_callback = NULL;
        }
        return;
    }
}

static void
gtk_clear_timers(void) {
    GTKTimer *timer = gtk_timers;
    gtk_timers = NULL;
    while (timer) {
        GTKTimer *next = timer->next;
        if (timer->source_id) g_source_remove(timer->source_id);
        if (timer->free_callback) timer->free_callback(timer->id, timer->callback_data);
        free(timer);
        timer = next;
    }
}

void
gtk_backend_run_main_loop(tick_callback_fun callback, void *callback_data) {
    gtk_tick_callback = callback;
    gtk_tick_callback_data = callback_data;
    gtk_main_loop = g_main_loop_new(NULL, false);
    // Keep GLFW's hidden windows and platform events serviced while GTK owns
    // the main context. Clipboard transfers use GDK's display connection.
    gtk_glfw_poll_source_id = g_timeout_add(8, gtk_poll_glfw_events, NULL);
    gtk_schedule_tick();
    g_main_loop_run(gtk_main_loop);
    if (gtk_glfw_poll_source_id) {
        g_source_remove(gtk_glfw_poll_source_id);
        gtk_glfw_poll_source_id = 0;
    }
    g_main_loop_unref(gtk_main_loop);
    gtk_main_loop = NULL;
    gtk_tick_callback = NULL;
    gtk_tick_callback_data = NULL;
    gtk_clear_timers();
}

void
gtk_backend_stop_main_loop(void) {
    if (gtk_main_loop) g_main_loop_quit(gtk_main_loop);
}
#else
bool is_gtk_backend_active(void) { return false; }
bool is_gtk_backend_enabled(void) { return false; }
void gtk_backend_wakeup(void) {}
id_type gtk_backend_add_main_loop_timer(monotonic_t UNUSED interval, bool UNUSED repeats, timer_callback_fun UNUSED callback, void UNUSED *callback_data, timer_callback_fun UNUSED free_callback) { return 0; }
void gtk_backend_update_main_loop_timer(id_type UNUSED timer_id, monotonic_t UNUSED interval, bool UNUSED enabled) {}
void gtk_backend_remove_main_loop_timer(id_type UNUSED timer_id) {}
void gtk_backend_run_main_loop(tick_callback_fun UNUSED callback, void UNUSED *callback_data) {}
void gtk_backend_stop_main_loop(void) {}
#endif
