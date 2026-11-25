// A toy nanobind module that uses polybind/dispatch.hpp with three kinds: two array kinds (uint8 and float) and a class.
// tests/test_dispatch_headers.py builds it (through CMake, against polybind::polybind) and runs tests/data/dispatch_checks.py.
#include <cstddef>
#include <cstdint>
#include <utility>
#include <stdexcept>
#include <string>
#include <string_view>
#include <vector>

#include <nanobind/nanobind.h>
#include <nanobind/ndarray.h>
#include <nanobind/stl/string.h>
#include <nanobind/stl/vector.h>

#include <polybind/nanobind.hpp>

namespace nb = nanobind;
using namespace nb::literals;

// ---- the project's data: a view of an array, and a class
template <class T>
struct Grid {
    int h = 0, w = 0;
    T* data = nullptr;
};
struct Pic {
    std::vector<int> v{1, 2, 3};
};

// A value that owns its memory: what a function that makes a new array gives back (a Grid is only a view).
template <class T>
struct Owned {
    int h = 0, w = 0;
    std::vector<T> v;
};

enum class mode { luma, each };
mode mode_from_name(std::string_view n) {
    if (n == "luma") return mode::luma;
    if (n == "each") return mode::each;
    throw std::invalid_argument("unknown mode \"" + std::string(n) + "\": use luma or each");
}

template <>
struct polybind::enum_names<mode> {
    static constexpr bool enabled = true;
    static mode from_name(std::string_view n) { return mode_from_name(n); }
};

// ---- how Python sees each kind
template <class T>
struct array_kind {
    using array_rw = polybind::strict<nb::ndarray<T, nb::c_contig, nb::device::cpu>>;
    using array_ro = polybind::strict<nb::ndarray<const T, nb::c_contig, nb::device::cpu>>;
    using const_arg = array_ro;
    using mutable_arg = array_rw;
    using mutable_ret = array_rw;
    static constexpr auto policy = nb::rv_policy::automatic;
    struct holder {
        Grid<T> g;
        Grid<T>& get() { return g; }
    };
    struct const_holder {
        Grid<T> g;
        std::size_t ndim;  // the result may have the shape of the argument
        explicit const_holder(const array_ro& a)
            : g{int(a.value.shape(0)), int(a.value.shape(1)), const_cast<T*>(a.value.data())}, ndim(a.value.ndim()) {}
        const Grid<T>& get() { return g; }
    };
    static holder mutable_holder(const array_rw& a) {
        return holder{Grid<T>{int(a.value.shape(0)), int(a.value.shape(1)), a.value.data()}};
    }
    static array_rw give_back(const array_rw& a) { return a; }
};
template <>
struct polybind::nanobind_backend::kind<Grid<std::uint8_t>> : array_kind<std::uint8_t> {};
template <>
struct polybind::nanobind_backend::kind<Grid<float>> : array_kind<float> {};
template <>
struct polybind::nanobind_backend::kind<Pic> {
    using const_arg = const Pic&;
    using mutable_arg = Pic&;
    using mutable_ret = Pic&;
    static constexpr auto policy = nb::rv_policy::none;
    struct holder {
        Pic& p;
        Pic& get() { return p; }
    };
    struct const_holder {
        const Pic& p;
        explicit const_holder(const Pic& x) : p(x) {}
        const Pic& get() { return p; }
    };
    static holder mutable_holder(Pic& p) { return holder{p}; }
    static Pic& give_back(Pic& p) { return p; }
    using new_ret = Pic;
    static constexpr auto new_policy = nb::rv_policy::automatic;
    static Pic wrap(Pic&& p) { return std::move(p); }
};
// A kind that is the result of a function: an owning array (the capsule frees it), or the class by value.
template <class T>
struct polybind::nanobind_backend::kind<Owned<T>> {
    using new_ret = nb::ndarray<nb::numpy, T>;
    static constexpr auto new_policy = nb::rv_policy::automatic;
    static new_ret wrap(Owned<T>&& o) {
        auto* p = new Owned<T>(std::move(o));
        nb::capsule owner(p, [](void* q) noexcept { delete static_cast<Owned<T>*>(q); });
        const std::size_t shape[2] = {std::size_t(p->h), std::size_t(p->w)};
        return new_ret(p->v.data(), 2, shape, owner);
    }
    // a result of the shape of the argument: (h, w, 1) for an argument of three dimensions
    template <class H, class = decltype(std::declval<const H&>().ndim)>
    static new_ret wrap(Owned<T>&& o, const H& like) {
        auto* p = new Owned<T>(std::move(o));
        nb::capsule owner(p, [](void* q) noexcept { delete static_cast<Owned<T>*>(q); });
        const std::size_t shape[3] = {std::size_t(p->h), std::size_t(p->w), 1};
        return new_ret(p->v.data(), like.ndim, shape, owner);
    }
};
inline constexpr polybind::kinds<Pic, Grid<std::uint8_t>, Grid<float>> my_kinds{};

// ---- the functions of the "library": one per kind
void add(Pic& p, int n, mode m) {
    for (int& x : p.v) x += n + int(m);
}
template <class T>
void add(Grid<T>& g, int n, mode m) {
    for (int i = 0; i < g.h * g.w; ++i) g.data[i] = T(g.data[i] + n + int(m));
}
Pic& add_ret(Pic& p, int n, mode m) {
    add(p, n, m);
    return p;
}
template <class T>
Grid<T>& add_ret(Grid<T>& g, int n, mode m) {
    add(g, n, m);
    return g;
}
template <class T>
void mix(Grid<T>& g, const Grid<T>& r) {
    for (int i = 0; i < g.h * g.w; ++i) g.data[i] = T(g.data[i] + r.data[i]);
}
void mix(Pic& p, const Pic& r) {
    for (std::size_t i = 0; i < p.v.size(); ++i) p.v[i] += r.v[i];
}

// functions that make a new value
template <class T>
Owned<T> scaled(const Grid<T>& g, int n, mode m) {
    Owned<T> o{g.h, g.w, std::vector<T>(std::size_t(g.h) * std::size_t(g.w))};
    for (std::size_t i = 0; i < o.v.size(); ++i) o.v[i] = T(g.data[i] * n + int(m));
    return o;
}
Pic scaled(const Pic& p, int n, mode m) {
    Pic o = p;
    for (int& x : o.v) x = x * n + int(m);
    return o;
}
// another kind than the argument's: a uint8 array from any array
template <class T>
Owned<std::uint8_t> bytes(const Grid<T>& g) {
    Owned<std::uint8_t> o{g.h, g.w, std::vector<std::uint8_t>(std::size_t(g.h) * std::size_t(g.w))};
    for (std::size_t i = 0; i < o.v.size(); ++i) o.v[i] = std::uint8_t(g.data[i]);
    return o;
}
Pic bytes(const Pic& p) {
    Pic o = p;
    for (int& x : o.v) x += 100;
    return o;
}
template <class T>
Owned<T> blended(const Grid<T>& a, const Grid<T>& b) {
    Owned<T> o{a.h, a.w, std::vector<T>(std::size_t(a.h) * std::size_t(a.w))};
    for (std::size_t i = 0; i < o.v.size(); ++i) o.v[i] = T(a.data[i] + b.data[i]);
    return o;
}
Pic blended(const Pic& a, const Pic& b) {
    Pic o = a;
    for (std::size_t i = 0; i < o.v.size(); ++i) o.v[i] += b.v[i];
    return o;
}

NB_MODULE(dispatch_module, m) {
    auto pic = nb::class_<Pic>(m, "Pic").def(nb::init<>()).def_prop_ro("v", [](const Pic& p) { return p.v; });
    nb::enum_<mode>(m, "Mode").value("LUMA", mode::luma).value("EACH", mode::each);

    // a void lambda, one generic parameter, an int and an enum
    polybind::def_dispatch(m, pic, "add_inplace", my_kinds, [](auto& x, int n, mode md) { add(x, n, md); }, "x"_a,
                           "n"_a, "mode"_a = "luma", "adds n");
    // a lambda that gives its argument back
    polybind::def_dispatch(m, pic, "add2_inplace", my_kinds, [](auto& x, int n, mode md) -> auto& { return add_ret(x, n, md); },
                           "x"_a, "n"_a, "mode"_a = "luma");
    // two generic parameters: a second value of the same kind
    polybind::def_dispatch(m, pic, "mix_inplace", my_kinds, [](auto& x, const auto& r) { mix(x, r); }, "x"_a, "other"_a);
    // a lambda that returns another object than its argument: found when it runs
    polybind::def_dispatch(m, pic, "bad_inplace", my_kinds, [](auto& x) -> auto& {
        static Pic other;
        if constexpr (std::is_same_v<std::decay_t<decltype(x)>, Pic>) return other;
        else return x;
    }, "x"_a);
    // the lint: it changes its argument, and its name does not end with _inplace
    polybind::def_dispatch(m, pic, "add3", my_kinds, [](auto& x, int n, mode md) { add(x, n, md); }, "x"_a, "n"_a,
                           "mode"_a = "luma");
    // a function that makes a new value: the same kind, another kind, a second value of the kind
    polybind::def_dispatch(m, pic, "scaled", my_kinds, [](const auto& x, int n, mode md) { return scaled(x, n, md); },
                           "x"_a, "n"_a, "mode"_a = "luma", "a new value");
    polybind::def_dispatch(m, pic, "bytes", my_kinds, [](const auto& x) { return bytes(x); }, "x"_a);
    polybind::def_dispatch(m, pic, "blended", my_kinds, [](const auto& x, const auto& r) { return blended(x, r); }, "x"_a,
                           "other"_a);
    // the lint the other way round: it does not change its argument, and its name ends with _inplace
    polybind::def_dispatch(m, pic, "peek_inplace", my_kinds, [](const auto& x) { return bytes(x); }, "x"_a);
}
