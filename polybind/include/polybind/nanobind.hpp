#pragma once

// polybind/nanobind.hpp: the nanobind backend of polybind/dispatch.hpp.
//
// A project says how each kind of data is seen from Python by specialising
//     template <> struct polybind::nanobind_backend::kind<K> { ... };
// with, for the kind K:
//     using const_arg    = the parameter type of an overload that only reads K;
//     using mutable_arg  = the parameter type of an overload that changes K (a non-const reference, or a writable array);
//     using mutable_ret  = what that overload gives back to Python: the argument itself (a reference, or the array);
//     static constexpr auto policy = nanobind::rv_policy::none (or automatic): how a returned reference becomes a Python
//                                    object; `auto`, because nanobind wants a compile-time tag, not an rv_policy value;
//     static auto mutable_holder(mutable_arg) -> an object with get() -> K&  (a view of the array, for example);
//     struct const_holder: made from a const_arg, with get() -> const K&;
//     static mutable_ret give_back(mutable_arg).
// A kind that a function can give back as a NEW value (a lambda with a `const auto&` first parameter returns it by value)
// has, besides the above (only const_arg and const_holder are needed to read it):
//     using new_ret = the type of the Python object made: a nanobind::ndarray<nanobind::numpy, T> that owns its memory
//                     (a capsule), or the class itself, returned by value;
//     static constexpr auto new_policy = nanobind::rv_policy::automatic (or move): the policy of that return;
//     static new_ret wrap(K&&): makes the Python object from the value (called with the GIL held). It may instead (or
//                     as well: this one is tried first) be `wrap(K&&, const H& like)`, where `like` is the const_holder of the
//                     argument: a result that has the shape of the argument (a one-channel array of shape (H, W, 1)) can ask it.
// A kind that is only ever a result needs just these three.
// An array parameter is spelled polybind::strict<nanobind::ndarray<...>>: it is received without conversion (a float64
// array does not become a uint8 one, nor a non-contiguous array a contiguous copy).
// and, for an enum E, by specialising polybind::enum_names<E> (see dispatch.hpp). An enum is then given by its name
// (any case) or as the enum itself; an unknown name raises ValueError with the message of from_name.

#include <cctype>
#include <string>
#include <type_traits>

#include <nanobind/nanobind.h>
#include <nanobind/stl/string.h>

// Ready-made kinds, so that a project writes no more than it must:
//     - a number (int, double, bool, ...) is a kind for the RESULT of a function (an Otsu level, a count): nothing to write;
//     - polybind::array_kind<Traits, T>: a matrix type of the project as a numpy array of T (see below).

#include <cstddef>
#include <stdexcept>
#include <type_traits>
#include <utility>

#include <nanobind/ndarray.h>

#include "dispatch.hpp"

namespace polybind {

struct nanobind_backend {
    template <class K, class = void>
    struct kind;  // specialised by the project

    using gil_release = nanobind::gil_scoped_release;

    template <class Cls, class K>
    static constexpr bool is_class_of = std::is_same_v<typename Cls::Type, K>;

    // A Python RuntimeWarning (an error under -W error, which is how the tests of a project run).
    static void warn(const std::string& message) {
        if (PyErr_WarnEx(PyExc_RuntimeWarning, message.c_str(), 1) < 0) throw nanobind::python_error();
    }

    // The module function. `first` is the descriptor of the data parameter (a nanobind arg, with no default). That the
    // data is not converted from another type is not done here, but by the type of the parameter: strict<...>.
    template <class Kind, class Scope, class Fn, class First, class... Extra>
    static void def_function(Scope& scope, const char* name, Fn& fn, const First& first, const Extra&... extra) {
        scope.def(name, fn, first, extra..., Kind::policy);
    }

    // The method of a class: the data parameter is `self`, so it has no descriptor.
    template <class Kind, class Cls, class Fn, class... Extra>
    static void def_method(Cls& cls, const char* name, Fn& fn, const Extra&... extra) {
        cls.def(name, fn, extra..., Kind::policy);
    }
};

template <>
struct backend_of<nanobind::module_> {
    using type = nanobind_backend;
};

// A number is a kind that a function can only give back: an int or a double or a bool is the Python number.
template <class K>
struct nanobind_backend::kind<K, std::enable_if_t<std::is_arithmetic_v<K>>> {
    using new_ret = K;
    static constexpr auto new_policy = nanobind::rv_policy::automatic;
    static K wrap(K&& value) { return value; }
};

// A matrix type of the project as a numpy array of T, in one line:
//     template <> struct polybind::nanobind_backend::kind<my::mat8u> : polybind::array_kind<my_traits, std::uint8_t> {};
// `Traits` says what the project's matrix is, with, for the element type T:
//     template <class T> using matrix = the matrix of T (a view of memory, or an owner: a view must not own the memory);
//     template <class T, class A> static matrix<T> view(const A& array)
//             a matrix that is a VIEW of the memory of a nanobind::ndarray (const or not) of shape (H, W) or (H, W, C); it checks
//             the shape and throws what it likes (std::invalid_argument is a ValueError);
//     template <class T> static T* data(matrix<T>&); static std::size_t height / width / channels(const matrix<T>&);
//     static bool empty(const matrix<T>&).
// The kind then has: the argument that is only read or that is changed (strict: a C-contiguous numpy array of exactly the
// type T, a read-only one for reading), and, for a function that gives back a NEW matrix, an owning array: the matrix moves to
// the heap and a capsule frees it with the array. The array has the shape of the argument: (H, W) for (H, W) and (H, W, 1)
// for (H, W, 1) when the matrix has one channel, and (H, W, C) when it has more (wrap(R&&, const holder&); without the
// holder, wrap(R&&): (H, W) for one channel).
template <class Traits, class T>
struct array_kind {
    using matrix = typename Traits::template matrix<T>;
    using array_rw = strict<nanobind::ndarray<T, nanobind::c_contig, nanobind::device::cpu>>;
    using array_ro = strict<nanobind::ndarray<const T, nanobind::c_contig, nanobind::device::cpu>>;
    using const_arg = array_ro;
    using mutable_arg = array_rw;
    using mutable_ret = array_rw;
    static constexpr auto policy = nanobind::rv_policy::automatic;

    struct holder {
        matrix m;
        matrix& get() { return m; }
    };
    struct const_holder {
        matrix m;
        bool three_d;  // the argument is (H, W, C): a result of one channel is (H, W, 1) too
        explicit const_holder(const array_ro& a) : m(Traits::template view<T>(a.value)), three_d(a.value.ndim() == 3) {}
        const matrix& get() { return m; }
    };
    static holder mutable_holder(const array_rw& a) { return holder{Traits::template view<T>(a.value)}; }
    static array_rw give_back(const array_rw& a) { return a; }

    using new_ret = nanobind::ndarray<nanobind::numpy, T>;
    static constexpr auto new_policy = nanobind::rv_policy::automatic;
    static new_ret make(matrix&& m, bool three_d) {
        if (Traits::empty(m)) throw std::runtime_error("the result is empty");
        auto* owned = new matrix(std::move(m));
        nanobind::capsule owner(owned, [](void* p) noexcept { delete static_cast<matrix*>(p); });
        const std::size_t shape[3] = {Traits::height(*owned), Traits::width(*owned), Traits::channels(*owned)};
        return new_ret(Traits::data(*owned), three_d || shape[2] != 1 ? 3 : 2, shape, owner);
    }
    template <class H, class = decltype(std::declval<const H&>().three_d)>
    static new_ret wrap(matrix&& m, const H& like) {
        return make(std::move(m), like.three_d);
    }
    static new_ret wrap(matrix&& m) { return make(std::move(m), false); }
};

}  // namespace polybind

namespace nanobind {
namespace detail {

// An enum as a name or as the enum itself. NOT noexcept: a bad name throws std::invalid_argument, which nanobind turns
// into ValueError (checked with nanobind 3.1; a noexcept from_python would terminate the process instead).
template <class E>
struct type_caster<polybind::named<E>> {
    NB_TYPE_CASTER(polybind::named<E>, make_caster<E>::Name + const_name(" | str"))

    bool from_python(handle src, uint8_t, cleanup_list*) {
        if (isinstance<E>(src)) {
            value.value = cast<E>(src);
            return true;
        }
        if (!PyUnicode_Check(src.ptr())) return false;
        std::string name = cast<std::string>(src);
        for (char& c : name) c = char(std::tolower(static_cast<unsigned char>(c)));
        value.value = polybind::enum_names<E>::from_name(name);
        return true;
    }

    static handle from_cpp(const polybind::named<E>&, rv_policy, cleanup_list*) noexcept { return handle(); }
};

// A parameter without implicit conversion: the type caster of T, with the conversion flag taken away. What is given back
// to Python is what T gives back (for an array that came from Python, that very object).
template <class T>
struct type_caster<polybind::strict<T>> {
    using Inner = make_caster<T>;
    NB_TYPE_CASTER(polybind::strict<T>, Inner::Name)

    bool from_python(handle src, uint8_t flags, cleanup_list* cleanup) noexcept {
        flags &= ~static_cast<uint8_t>(cast_flags::convert);
        Inner inner;
        if (!inner.from_python(src, flags, cleanup)) return false;
        value.value = static_cast<T&>(inner);
        return true;
    }

    static handle from_cpp(const polybind::strict<T>& v, rv_policy policy, cleanup_list* cleanup) noexcept {
        return Inner::from_cpp(v.value, policy, cleanup);
    }
};

}  // namespace detail
}  // namespace nanobind
