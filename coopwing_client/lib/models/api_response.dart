class ApiResponse<T> {
  const ApiResponse._({required this.ok, this.data, this.error});

  const ApiResponse.success(T data) : this._(ok: true, data: data);

  const ApiResponse.failure(ApiError error) : this._(ok: false, error: error);

  final bool ok;
  final T? data;
  final ApiError? error;

  Map<String, Object?> toJson(Object? Function(T value) encodeData) {
    return {
      'ok': ok,
      if (ok) 'data': data == null ? null : encodeData(data as T),
      if (!ok) 'error': error?.toJson(),
    };
  }
}

class ApiError {
  const ApiError({
    required this.code,
    required this.message,
    this.details = const {},
  });

  final String code;
  final String message;
  final Map<String, Object?> details;

  factory ApiError.fromJson(Map<String, Object?> json) {
    return ApiError(
      code: json['code'] as String? ?? 'INTERNAL_ERROR',
      message: json['message'] as String? ?? 'Unexpected backend error.',
      details: json['details'] as Map<String, Object?>? ?? const {},
    );
  }

  Map<String, Object?> toJson() {
    return {'code': code, 'message': message, 'details': details};
  }
}
