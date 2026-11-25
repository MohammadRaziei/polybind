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

// ---- the project's data: a matrix that is a view of memory (a function reads or changes an array) or an owner (a function makes
// a new array), and a class
template <class T>
struct Mat {
    int h = 0, w = 0, c = 1;
    T* data = nullptr;
    std::vector<T> store;  // empty for a view
    Mat() = default;
    Mat(int h_, int w_, int c_, T* view) : h(h_), w(w_), c(c_), data(view) {}
    Mat(int h_, int w_, int c_) : h(h_), w(w_), c(c_), store(std::size_t(h_) * std::size_t(w_) * std::size_t(c_)) {
        data = store.data();
    }
    Mat(Mat&&) noexcept = default;  // the buffer of `store` stays where it is
    Mat& operator=(Mat&&) noexcept = default;
    Mat(const Mat&) = delete;
    Mat& operator=(const Mat&) = delete;
};
struct Pic {
    std::vector<int> v{1, 2, 3};
};

// What polybind::array_kind needs to know about the matrix
struct MatTraits {
    template <class T>
    using matrix = Mat<T>;
    template <class T, class A>
    static Mat<T> view(const A& a) {
        if (a.ndim() != 2 && a.ndim() != 3) throw std::invalid_argument("an array of shape (H, W) or (H, W, C) is expected");
        return Mat<T>(int(a.shape(0)), int(a.shape(1)), a.ndim() == 3 ? int(a.shape(2)) : 1, const_cast<T*>(a.data()));
    }
    template <class T>
    static T* data(Mat<T>& m) { return m.data; }
    template <class T>
    static std::size_t height(const Mat<T>& m) { return std::size_t(m.h); }
    template <class T>
    static std::size_t width(const Mat<T>& m) { return std::size_t(m.w); }
    template <class T>
    static std::size_t channels(const Mat<T>& m) { return std::size_t(m.c); }
    template <class T>
    static bool empty(const Mat<T>& m) { return m.h == 0 || m.w == 0 || m.c == 0; }
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

// ---- how Python sees each kind: two matrices in one line each (polybind::array_kind), and a class
template <>
struct polybind::nanobind_backend::kind<Mat<std::uint8_t>> : polybind::array_kind<MatTraits, std::uint8_t> {};
template <>
struct polybind::nanobind_backend::kind<Mat<float>> : polybind::array_kind<MatTraits, float> {};
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
inline constexpr polybind::kinds<Pic, Mat<std::uint8_t>, Mat<float>> my_kinds{};

// ---- the functions of the "library": one per kind
void add(Pic& p, int n, mode m) {
    for (int& x : p.v) x += n + int(m);
}
template <class T>
void add(Mat<T>& g, int n, mode m) {
    for (int i = 0; i < g.h * g.w * g.c; ++i) g.data[i] = T(g.data[i] + n + int(m));
}
Pic& add_ret(Pic& p, int n, mode m) {
    add(p, n, m);
    return p;
}
template <class T>
Mat<T>& add_ret(Mat<T>& g, int n, mode m) {
    add(g, n, m);
    return g;
}
template <class T>
void mix(Mat<T>& g, const Mat<T>& r) {
    for (int i = 0; i < g.h * g.w * g.c; ++i) g.data[i] = T(g.data[i] + r.data[i]);
}
void mix(Pic& p, const Pic& r) {
    for (std::size_t i = 0; i < p.v.size(); ++i) p.v[i] += r.v[i];
}

// functions that make a new value
template <class T>
Mat<T> scaled(const Mat<T>& g, int n, mode m) {
    Mat<T> o(g.h, g.w, g.c);
    for (int i = 0; i < g.h * g.w * g.c; ++i) o.data[i] = T(g.data[i] * n + int(m));
    return o;
}
Pic scaled(const Pic& p, int n, mode m) {
    Pic o = p;
    for (int& x : o.v) x = x * n + int(m);
    return o;
}
// another kind than the argument's: a uint8 array from any array
template <class T>
Mat<std::uint8_t> bytes(const Mat<T>& g) {
    Mat<std::uint8_t> o(g.h, g.w, g.c);
    for (int i = 0; i < g.h * g.w * g.c; ++i) o.data[i] = std::uint8_t(g.data[i]);
    return o;
}
Pic bytes(const Pic& p) {
    Pic o = p;
    for (int& x : o.v) x += 100;
    return o;
}
template <class T>
Mat<T> blended(const Mat<T>& a, const Mat<T>& b) {
    Mat<T> o(a.h, a.w, a.c);
    for (int i = 0; i < a.h * a.w * a.c; ++i) o.data[i] = T(a.data[i] + b.data[i]);
    return o;
}
Pic blended(const Pic& a, const Pic& b) {
    Pic o = a;
    for (std::size_t i = 0; i < o.v.size(); ++i) o.v[i] += b.v[i];
    return o;
}
// a number, not an array: an int for the bytes and a double for the floats; a three-channel array of the same size
template <class T>
auto total(const Mat<T>& g) {
    long long sum = 0;
    for (int i = 0; i < g.h * g.w * g.c; ++i) sum += (long long)g.data[i];
    if constexpr (std::is_floating_point_v<T>) return double(sum);
    else return int(sum);
}
int total(const Pic& p) {
    int sum = 0;
    for (int x : p.v) sum += x;
    return sum;
}
template <class T>
Mat<T> tripled(const Mat<T>& g) {  // a result of more channels than the argument: always (H, W, 3)
    Mat<T> o(g.h, g.w, 3);
    for (int i = 0; i < g.h * g.w; ++i)
        for (int k = 0; k < 3; ++k) o.data[3 * i + k] = g.data[i * g.c];
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
    // a number as the result: an int for a uint8 array and a Pic, a double for a float array
    polybind::def_dispatch(m, pic, "total", my_kinds, [](const auto& x) { return total(x); }, "x"_a);
    // a result of three channels, whatever the argument has
    polybind::def_dispatch(m, pic, "tripled", polybind::kinds<Mat<std::uint8_t>, Mat<float>>{}, [](const auto& x) { return tripled(x); },
                           "x"_a);
    polybind::def_dispatch(m, pic, "peek_inplace", my_kinds, [](const auto& x) { return bytes(x); }, "x"_a);
}
