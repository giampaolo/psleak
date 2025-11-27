#include <Python.h>
#include <stdlib.h>
#if defined(PSLEAK_WINDOWS)
#include <windows.h>
#else
#include <pthread.h>
#include <stdio.h>
#include <sys/mman.h>
#include <unistd.h>
#endif


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


// ====================================================================
// POSIX
// ====================================================================


#if defined(PSLEAK_POSIX)
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
#endif


// ====================================================================
// Windows
// ====================================================================


#if defined(PSLEAK_WINDOWS)
PyObject *
psleak_HeapAlloc(PyObject *self, PyObject *args) {
    void *ptr;
    size_t size;
    HANDLE heap;

    if (!PyArg_ParseTuple(args, "n", &size))
        return NULL;

    heap = GetProcessHeap();
    if (!heap)
        return PyErr_SetFromWindowsErr(0);

    ptr = HeapAlloc(heap, 0, size);
    if (!ptr)
        return PyErr_NoMemory();

    return PyLong_FromVoidPtr(ptr);
}


PyObject *
psleak_HeapFree(PyObject *self, PyObject *args) {
    void *ptr;
    PyObject *ptr_obj;
    HANDLE heap;

    if (!PyArg_ParseTuple(args, "O", &ptr_obj))
        return NULL;

    heap = GetProcessHeap();
    if (!heap)
        return PyErr_SetFromWindowsErr(0);

    ptr = PyLong_AsVoidPtr(ptr_obj);
    if (ptr == NULL && PyErr_Occurred())
        return NULL;

    if (!HeapFree(GetProcessHeap(), 0, ptr))
        return PyErr_SetFromWindowsErr(0);

    Py_RETURN_NONE;
}


PyObject *
psleak_VirtualAlloc(PyObject *self, PyObject *args) {
    SIZE_T size;
    void *ptr;

    if (!PyArg_ParseTuple(args, "n", &size))
        return NULL;

    ptr = VirtualAlloc(NULL, size, MEM_COMMIT | MEM_RESERVE, PAGE_READWRITE);

    if (ptr == NULL)
        return PyErr_SetFromWindowsErr(0);
    return PyLong_FromVoidPtr(ptr);
}


PyObject *
psleak_VirtualFree(PyObject *self, PyObject *args) {
    PyObject *ptr_obj;
    void *ptr;

    if (!PyArg_ParseTuple(args, "O", &ptr_obj))
        return NULL;

    ptr = PyLong_AsVoidPtr(ptr_obj);
    if (ptr == NULL && PyErr_Occurred())
        return NULL;

    // MEM_RELEASE requires size = 0
    if (!VirtualFree(ptr, 0, MEM_RELEASE))
        return PyErr_SetFromWindowsErr(0);

    Py_RETURN_NONE;
}


PyObject *
psleak_HeapCreate(PyObject *self, PyObject *args) {
    SIZE_T initial_size;
    SIZE_T max_size;
    HANDLE heap;

    if (!PyArg_ParseTuple(args, "nn", &initial_size, &max_size))
        return NULL;

    heap = HeapCreate(0, initial_size, max_size);
    if (heap == NULL) {
        PyErr_SetFromWindowsErr(0);
        return NULL;
    }

    return PyLong_FromVoidPtr(heap);
}

PyObject *
psleak_HeapDestroy(PyObject *self, PyObject *args) {
    PyObject *heap_obj;
    HANDLE heap;

    if (!PyArg_ParseTuple(args, "O", &heap_obj))
        return NULL;

    heap = (HANDLE)PyLong_AsVoidPtr(heap_obj);
    if (heap == NULL && PyErr_Occurred())
        return NULL;

    if (!HeapDestroy(heap)) {
        PyErr_SetFromWindowsErr(0);
        return NULL;
    }

    Py_RETURN_NONE;
}
#endif


// ====================================================================
// Threads
// ====================================================================


static volatile int stop_event = 0;

#ifdef PSLEAK_WINDOWS
DWORD WINAPI
thread_worker(LPVOID arg) {
    while (!stop_event) {
        Sleep(100);  // 0.1s
    }
    return 0;
}
#else
void *
thread_worker(void *arg) {
    while (!stop_event) {
        usleep(100000);  // 0.1s
    }
    return NULL;
}
#endif


// Start a native C thread (outside of Python territory), return handle
// as Python int.
PyObject *
start_native_thread(PyObject *self, PyObject *args) {
    stop_event = 0;

#ifdef PSLEAK_WINDOWS
    HANDLE hThread = CreateThread(NULL, 0, thread_worker, NULL, 0, NULL);

    if (hThread == NULL) {
        PyErr_SetString(PyExc_RuntimeError, "CreateThread failed");
        return NULL;
    }
    return PyLong_FromVoidPtr(hThread);
#else
    pthread_t tid;

    if (pthread_create(&tid, NULL, thread_worker, NULL) != 0) {
        PyErr_SetString(PyExc_RuntimeError, "pthread_create failed");
        return NULL;
    }
    return PyLong_FromUnsignedLong((unsigned long)tid);
#endif
}


// Stop a native thread by handle and wait until it exits.
PyObject *
stop_native_thread(PyObject *self, PyObject *args) {
#ifdef PSLEAK_WINDOWS
    void *handle;

    if (!PyArg_ParseTuple(args, "O", &handle))
        return NULL;

    HANDLE hThread = PyLong_AsVoidPtr(handle);
    stop_event = 1;
    WaitForSingleObject(hThread, INFINITE);
    CloseHandle(hThread);
#else
    unsigned long handle;

    if (!PyArg_ParseTuple(args, "k", &handle))
        return NULL;

    pthread_t tid = (pthread_t)handle;
    stop_event = 1;
    pthread_join(tid, NULL);
#endif

    Py_RETURN_NONE;
}


// ====================================================================
// Python idioms
// ====================================================================


// Deliberate leak: creates a list but never decrefs it.
PyObject *
leak_list(PyObject *self, PyObject *args) {
    size_t size;
    PyObject *py_list;

    if (!PyArg_ParseTuple(args, "n", &size))
        return NULL;
    py_list = PyList_New(size);  // new reference
    if (!py_list)
        return NULL;
    // Normally you'd Py_DECREF(py_list) before returning. Here we just
    // return None and leak py_list.
    Py_RETURN_NONE;
}


// ====================================================================


static PyMethodDef TestExtMethods[] = {
    {"free", psleak_free, METH_VARARGS, ""},
    {"leak_list", leak_list, METH_VARARGS, ""},
    {"malloc", psleak_malloc, METH_VARARGS, ""},
    {"start_native_thread", start_native_thread, METH_VARARGS, ""},
    {"stop_native_thread", stop_native_thread, METH_VARARGS, ""},
#if defined(PSLEAK_POSIX)
    {"mmap", psleak_mmap, METH_VARARGS, ""},
    {"munmap", psleak_munmap, METH_VARARGS, ""},
#else
    {"HeapAlloc", psleak_HeapAlloc, METH_VARARGS, ""},
    {"HeapCreate", psleak_HeapCreate, METH_VARARGS, ""},
    {"HeapDestroy", psleak_HeapDestroy, METH_VARARGS, ""},
    {"HeapFree", psleak_HeapFree, METH_VARARGS, ""},
    {"VirtualAlloc", psleak_VirtualAlloc, METH_VARARGS, ""},
    {"VirtualFree", psleak_VirtualFree, METH_VARARGS, ""},
#endif
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
