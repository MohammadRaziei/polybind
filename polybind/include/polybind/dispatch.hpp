#pragma once

// polybind/dispatch.hpp: one Python name for a function that works on several kinds of data.
//
// A C++ library has templates (or overloads) for each kind of data it handles, and a binding library sees every one
// as a function of its own. A Python user wants one name that takes whichever kind of data it has. def_dispatch()
// registers the overloads of one function, one per kind, from ONE generic lambda:
//
//     polybind::def_dispatch(m, image_cls, "equalize_inplace", my_kinds,
//         [](auto& x, my::tone_mode mode) -> auto& { return my::equalize_inplace(x, mode); },
//         "img"_a, "mode"_a = "luma", "doc");
//
// - `my_kinds` is a polybind::kinds<A, B, C> value: the C++ types the function is made for, in the order the
//   overloads are tried. The lambda is instantiated once for each of them; a Python argument of any other type
//   matches no overload and the binding library reports a TypeError. The project says how each kind is seen from
//   Python by specialising the kind<K> template of the backend it uses (see polybind/nanobind.hpp).
// - The first parameter of the lambda is `auto&`: the function changes its argument and gives it back. It must
//   return void or a reference to that argument (the call then chains: the same Python object comes out). The other
//   parameters have concrete types (so that they are converted from Python before the lambda runs and the GIL can be
//   released around it, which is done for every call), with two exceptions:
//     * an enum E for which enum_names<E> is specialised is given by its name or as the enum itself;
//     * a second `const auto&` parameter is a second value of the same kind as the first.
// - The names are checked when they are registered (POLYBIND_LINT, on by default): a name that ends with _inplace
//   must belong to a lambda that takes `auto&`, and the other way round.
//
// Nothing in this file knows about a binding library or about the project: the backend (polybind/nanobind.hpp) and
// the kinds come from outside. The functions that make a new value of the same kind (a `const auto&` first
// parameter) are not supported yet.

#include <cstddef>
#include <stdexcept>
#include <string>
#include <string_view>
#include <tuple>
#include <type_traits>
#include <utility>

#ifndef POLYBIND_LINT
#define POLYBIND_LINT 1
#endif

namespace polybind {

// The kinds of data a function is made for, in the order its overloads are tried.
template <class... Ks>
struct kinds {};

// The name of an enum E, to be specialised by the project for each enum a lambda takes:
//     template <> struct polybind::enum_names<my::tone_mode> {
//         static constexpr bool enabled = true;
//         static my::tone_mode from_name(std::string_view lower_case_name) { return my::tone_mode_from_name(...); }
//     };
// from_name throws std::invalid_argument (a ValueError in Python) for an unknown name.
template <class E, class = void>
struct enum_names {
    static constexpr bool enabled = false;
};

// An enum as the backend receives it: a name or the enum itself, turned into the enum before the call.
template <class E>
struct named {
    E value{};
    operator E() const { return value; }
};

// A parameter that must be of exactly the type it says: no implicit conversion. For an array this is what keeps a
// float64 or a non-contiguous array from being converted into a kind it is not: the kinds of the arrays use
// strict<array type> for every parameter that is an array. Backends give it a type caster that refuses conversions.
template <class T>
struct strict {
    T value;
};

// The backend of a scope (a module): specialised by polybind/nanobind.hpp.
template <class Scope>
struct backend_of;

namespace detail {

// ---- the signature of a generic lambda, as it is for one kind K: the address of operator()<K> (or <K, K> for a
// lambda with two generic parameters)
template <class Mem>
struct member_sig;
template <class R, class C, class A0, class... Rest>
struct member_sig<R (C::*)(A0, Rest...) const> {
    using ret = R;
    using first = A0;
    using rest = std::tuple<Rest...>;
};

template <class L, class K, class = void>
struct has_one_generic : std::false_type {};
template <class L, class K>
struct has_one_generic<L, K, std::void_t<decltype(&L::template operator()<K>)>> : std::true_type {};

template <class L, class K, bool One = has_one_generic<L, K>::value>
struct sig_for;
template <class L, class K>
struct sig_for<L, K, true> : member_sig<decltype(&L::template operator()<K>)> {
    static constexpr int generic = 1;
};
template <class L, class K>
struct sig_for<L, K, false> : member_sig<decltype(&L::template operator()<K, K>)> {
    static constexpr int generic = 2;
};

template <class L, class K>
inline constexpr bool changes_its_argument_v =
    !std::is_const_v<std::remove_reference_t<typename sig_for<L, K>::first>> &&
    std::is_lvalue_reference_v<typename sig_for<L, K>::first>;

// ---- lint: the name and the signature must say the same
inline bool ends_with(std::string_view s, std::string_view suffix) {
    return s.size() >= suffix.size() && s.substr(s.size() - suffix.size()) == suffix;
}

// An empty string when the name and the signature agree, else what is wrong.
inline std::string lint_message(std::string_view name, bool changes_argument) {
    const bool says_inplace = ends_with(name, "_inplace");
    if (says_inplace == changes_argument) return {};
    return "polybind: '" + std::string(name) +
           (says_inplace ? "' ends with _inplace, but its lambda takes the first parameter as const (it does not change it)"
                         : "' changes its first parameter (it is not const), but its name does not end with _inplace");
}

// ---- the parameters after the first one: how a backend receives each one, and what the lambda is given
//   py_type : the type of the parameter of the registered overload (what the binding library converts)
//   holder  : what is kept during the call (a view, an enum value...), made by make(py_type)
//   get()   : the value the lambda is called with
template <class B, class K, class R, bool Peers, class = void>
struct param {  // any other type: passed as it is
    using py_type = R;
    struct holder {
        R value;
        R get() { return static_cast<R>(value); }
    };
    static holder make(py_type v) { return holder{static_cast<R>(v)}; }
};

template <class B, class K, class R, bool Peers>
struct param<B, K, R, Peers, std::enable_if_t<enum_names<std::decay_t<R>>::enabled>> {  // an enum
    using E = std::decay_t<R>;
    using py_type = named<E>;
    struct holder {
        E value;
        E get() { return value; }
    };
    static holder make(const py_type& v) { return holder{v.value}; }
};

template <class B, class K, class R>
struct param<B, K, R, true, std::enable_if_t<std::is_same_v<std::decay_t<R>, K> && !enum_names<std::decay_t<R>>::enabled>> {
    // a second value of the kind K (a `const auto&` parameter of a lambda with two generic parameters)
    using kind = typename B::template kind<K>;
    using py_type = typename kind::const_arg;
    using holder = typename kind::const_holder;
    static holder make(py_type v) { return holder(v); }
};

// ---- one overload of one function: the kind K
template <class B, class K, class F, class... Rest>
auto make_overload(F f, const char* name, std::tuple<Rest...>*) {
    using S = sig_for<F, K>;
    constexpr bool peers = S::generic == 2;
    using kind = typename B::template kind<K>;
    using R = typename S::ret;
    static_assert(std::is_void_v<R> || std::is_same_v<R, K&>,
                  "polybind: a function that changes its argument returns void or a reference to it (K&)");
    return [f, name](typename kind::mutable_arg x, typename param<B, K, Rest, peers>::py_type... rest) ->
           typename kind::mutable_ret {
        auto hx = kind::mutable_holder(x);
        std::tuple<typename param<B, K, Rest, peers>::holder...> h{param<B, K, Rest, peers>::make(rest)...};
        bool same = true;
        {
            typename B::gil_release release;
            if constexpr (std::is_void_v<R>) {
                std::apply([&](auto&... p) { f(hx.get(), p.get()...); }, h);
            } else {
                K& out = std::apply([&](auto&... p) -> K& { return f(hx.get(), p.get()...); }, h);
                same = (&out == &hx.get());
            }
        }
        if (!same)
            throw std::logic_error(std::string("polybind: the in-place function '") + name +
                                   "' returned another object than the one it was given");
        return kind::give_back(x);
    };
}

// The arguments the user gave after the name and the lambda: the descriptor of the first parameter (which is
// the one the data comes in), then the others (descriptors, a doc string...).
template <class B, class K, class Scope, class Cls, class F, class First, class... Extra>
void def_kind(Scope& scope, Cls& cls, const char* name, F f, First&& first, Extra&&... extra) {
    using S = sig_for<F, K>;
    static_assert(changes_its_argument_v<F, K>,
                  "polybind: only functions that change their argument (auto& first parameter) are supported");
    using kind = typename B::template kind<K>;
    auto overload = make_overload<B, K>(f, name, static_cast<typename S::rest*>(nullptr));
    B::template def_function<kind>(scope, name, overload, first, extra...);
    if constexpr (B::template is_class_of<Cls, K>) B::template def_method<kind>(cls, name, overload, extra...);
}

}  // namespace detail

// Registers `name` on the module `scope` for every kind of `Ks`, and as a method of `cls` when `cls` is the class
// of one of the kinds. `extra` is what the binding library takes after the function: the descriptor of the data
// parameter first (a nanobind arg, with no default), then those of the other parameters and the doc string.
template <class Scope, class Cls, class... Ks, class F, class First, class... Extra>
void def_dispatch(Scope& scope, Cls& cls, const char* name, kinds<Ks...>, F f, First&& first, Extra&&... extra) {
    using B = typename backend_of<std::decay_t<Scope>>::type;
#if POLYBIND_LINT
    {
        using FirstKind = std::tuple_element_t<0, std::tuple<Ks...>>;
        const std::string message = detail::lint_message(name, detail::changes_its_argument_v<F, FirstKind>);
        if (!message.empty()) B::warn(message);
    }
#endif
    (detail::def_kind<B, Ks>(scope, cls, name, f, first, extra...), ...);
}

}  // namespace polybind
