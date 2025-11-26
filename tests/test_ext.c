#include <Python.h>
#include <stdlib.h>
#include <sys/mman.h>


static PyObject *
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

static PyObject *
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
static PyObject *
psleak_mmap(PyObject *self, PyObject *args) {
    size_t size;
    if (!PyArg_ParseTuple(args, "n", &size)) {
        return NULL;
    }

    void *ptr = mmap(
        NULL, size, PROT_READ | PROT_WRITE, MAP_PRIVATE | MAP_ANONYMOUS, -1, 0
    );
    if (ptr == MAP_FAILED) {
        PyErr_SetFromErrno(PyExc_OSError);
        return NULL;
    }

    return PyLong_FromVoidPtr(ptr);
}


static PyMethodDef TestExtMethods[] = {
    {"malloc", psleak_malloc, METH_VARARGS, ""},
    {"free", psleak_free, METH_VARARGS, ""},
    {"mmap", psleak_mmap, METH_VARARGS, ""},
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
