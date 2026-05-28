module top (
    n0,
    n1,
    n2,
    n3,
    n4,
    n5,
    n6,
    n7,
    n8,
    n20,
    n22,
    n21,
    n31,
    n32,
    n23
);

  input n0;
  input n1;
  input n2;
  input n3;
  input n4;
  input n5;
  input n6;
  input n7;
  input n8;
  output n20;
  output n22;
  output n21;
  output n31;
  output n32;
  output n23;

  wire \$and$testcase/test53/test53.v:10$4_Y , \$or$testcase/test53/test53.v:11$6_Y , \$xor$testcase/test53/test53.v:12$8_Y , n100, n101, n102, n103, n104, n105, n107, n108, n109, n110, n111, n114, n120, n121, n122, n123, n128, n129, n130, n140, n141, n142;

  and   \$and$testcase/test53/test53.v:14$10  (n107, n2, n3);
  and   \$and$testcase/test53/test53.v:17$13  (n109, n2, n3);
  and   \$and$testcase/test53/test53.v:18$14  (n110, n2, n3);
  and   \$and$testcase/test53/test53.v:28$18  (n120, n2, n3);
  and   \$and$testcase/test53/test53.v:6$1  (n100, n2, n3);
  and   \$and$testcase/test53/test53.v:29$19  (n121, n2, n4);
  not   \$not$testcase/test53/test53.v:19$28  (n111, n4);
  not   \$not$testcase/test53/test53.v:22$30  (n114, n4);
  and   \$and$testcase/test53/test53.v:30$20  (n122, n2, n5);
  and   \$and$testcase/test53/test53.v:31$21  (n123, n2, n6);
  and   \$and$testcase/test53/test53.v:34$24  (n140, n6, n7);
  or    \$or$testcase/test53/test53.v:15$11  (n108, n107, n5);
  or    \$or$testcase/test53/test53.v:7$2  (n101, n100, n4);
  or    \$or$testcase/test53/test53.v:32$22  (n128, n120, n121);
  or    \$or$testcase/test53/test53.v:24$15  (n22, n114, n100);
  xor   \$xor$testcase/test53/test53.v:33$23  (n129, n122, n123);
  or    \$or$testcase/test53/test53.v:35$25  (n141, n140, n8);
  xor   \$xor$testcase/test53/test53.v:16$12  (n21, n108, n6);
  not   \$not$testcase/test53/test53.v:8$27  (n102, n101);
  and   \$and$testcase/test53/test53.v:38$26  (n130, n129, n128);
  not   \$not$testcase/test53/test53.v:36$31  (n142, n141);
  xor   \$xor$testcase/test53/test53.v:9$3  (n103, n102, n5);
  dff g72 (.Q(n31), .RN(n1), .SN(1'b1), .CK(n0), .D(n130));
  and   \$and$testcase/test53/test53.v:10$4  (\$and$testcase/test53/test53.v:10$4_Y , n103, n6);
  not   \$not$testcase/test53/test53.v:10$5  (n104, \$and$testcase/test53/test53.v:10$4_Y );
  or    \$or$testcase/test53/test53.v:11$6  (\$or$testcase/test53/test53.v:11$6_Y , n104, n7);
  not   \$not$testcase/test53/test53.v:11$7  (n105, \$or$testcase/test53/test53.v:11$6_Y );
  xor   \$xor$testcase/test53/test53.v:12$8  (\$xor$testcase/test53/test53.v:12$8_Y , n105, n8);
  not   \$not$testcase/test53/test53.v:12$9  (n20, \$xor$testcase/test53/test53.v:12$8_Y );
  dff g73 (.Q(n32), .RN(n1), .SN(1'b1), .CK(n0), .D(n20));

  assign n23 = 1'b1;

endmodule

(* blackbox *) module dff(input RN, input SN, input CK, input D, output Q);
endmodule
