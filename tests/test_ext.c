#include <Python.h>
#include <stdlib.h>
#include <sys/mman.h>


PyObject *
psleak_malloc(PyObject *self, PyObject *args) {
    size_t size;
    void *ptr;

    if (!PyArg_ParseTuple(args, "n", &size))
        return NULL;

    ptr = malloc(size);
    if (!ptr)
        return PyErr_NoMemory();

    // return pointer as integer
    return PyLong_FromVoidPtr(ptr);
}

PyObject *
psleak_free(PyObject *self, PyObject *args) {
    PyObject *ptr_obj;
    void *ptr;

    if (!PyArg_ParseTuple(args, "O", &ptr_obj))
        return NULL;

    ptr = PyLong_AsVoidPtr(ptr_obj);  // extract pointer

    // optionally check for errors
    if (ptr == NULL && PyErr_Occurred())
        return NULL;

    free(ptr);
    Py_RETURN_NONE;
}


// mmap wrapper: returns pointer to allocated memory
PyObject *
psleak_mmap(PyObject *self, PyObject *args) {
    size_t size;
    if (!PyArg_ParseTuple(args, "n", &size))
        return NULL;

    void *ptr = mmap(
        NULL, size, PROT_READ | PROT_WRITE, MAP_PRIVATE | MAP_ANONYMOUS, -1, 0
    );

    if (ptr == MAP_FAILED) {
        PyErr_SetFromErrno(PyExc_OSError);
        return NULL;
    }

    return PyLong_FromVoidPtr(ptr);
}

// munmap wrapper: takes pointer and size
PyObject *
psleak_munmap(PyObject *self, PyObject *args) {
    PyObject *ptr_obj;
    size_t size;
    void *ptr;

    if (!PyArg_ParseTuple(args, "On", &ptr_obj, &size))
        return NULL;

    ptr = PyLong_AsVoidPtr(ptr_obj);
    if (ptr == NULL && PyErr_Occurred())
        return NULL;

    if (munmap(ptr, size) != 0) {
        PyErr_SetFromErrno(PyExc_OSError);
        return NULL;
    }

    Py_RETURN_NONE;
}


// Deliberate leak: creates a list but never decrefs it.
PyObject *
leak_list(PyObject *self, PyObject *args) {
    PyObject *py_list = PyList_New(100);  // new reference

    if (!py_list)
        return NULL;
    // Normally you'd Py_DECREF(py_list) before returning. Here we just
    // return None and leak py_list.
    Py_RETURN_NONE;
}


static PyMethodDef TestExtMethods[] = {
    {"free", psleak_free, METH_VARARGS, ""},
    {"leak_list", leak_list, METH_VARARGS, ""},
    {"malloc", psleak_malloc, METH_VARARGS, ""},
    {"mmap", psleak_mmap, METH_VARARGS, ""},
    {"munmap", psleak_munmap, METH_VARARGS, ""},
    {NULL, NULL, 0, NULL}
};


static struct PyModuleDef testextmodule = {
    PyModuleDef_HEAD_INIT,
    "test_ext",  // module name
    "Test C extension",  // docstring
    -1,
    TestExtMethods
};


PyMODINIT_FUNC
PyInit_test_ext(void) {
    return PyModule_Create(&testextmodule);
}
