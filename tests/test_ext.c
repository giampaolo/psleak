#include <Python.h>
#include <stdlib.h>


static PyObject *
psleak_malloc(PyObject *self, PyObject *args) {
    size_t size;
    void *ptr;

    if (!PyArg_ParseTuple(args, "n", &size))
        return NULL;

    ptr = malloc(size);
    if (!ptr)
        return PyErr_NoMemory();

    // return the pointer as an integer
    return PyLong_FromVoidPtr(ptr);
}


static PyObject *
psleak_free(PyObject *self, PyObject *args) {
    void *ptr;

    if (!PyArg_ParseTuple(args, "O&", PyLong_AsVoidPtr, &ptr))
        return NULL;
    free(ptr);
    Py_RETURN_NONE;
}


static PyMethodDef TestExtMethods[] = {
    {"malloc", psleak_malloc, METH_VARARGS, ""},
    {"free", psleak_free, METH_VARARGS, ""},
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
