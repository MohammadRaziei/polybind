// A toy nanobind module that uses polybind/dispatch.hpp with three kinds: two array kinds (uint8 and float) and a class.
// tests/test_dispatch_headers.py builds it (through CMake, against polybind::polybind) and runs tests/data/dispatch_checks.py.
#include <cstdint>
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
        explicit const_holder(const array_ro& a)
            : g{int(a.value.shape(0)), int(a.value.shape(1)), const_cast<T*>(a.value.data())} {}
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
}
